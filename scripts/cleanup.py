"""Tear down the project's AWS resources, in dependency order.

Resources are found by the names used in the project (not by stored IDs), so
the script also works after recreating them. By default it is a DRY RUN that
only lists what it would delete; pass --execute to delete. Every step
tolerates resources that are already gone, so it is safe to re-run after a
partial failure.

The AgentCore Runtime is not deleted here: run `agentcore destroy` first
(from starter/). It removes the runtime, its endpoint, its execution role and
the deployment artifacts. Until it is gone this script skips the deployment
bucket and the runtime log groups.

IAM roles are only deleted when they come from one of the resources below
(never matched by name pattern), together with their inline policies and
the customer-managed policies attached only to them.

Usage (from the repository root):
  uv run --project starter python scripts/cleanup.py            # dry run
  uv run --project starter python scripts/cleanup.py --execute
"""
import argparse
import time

import boto3
from botocore.exceptions import ClientError

from project_config import main_settings

# Resource names used when the infrastructure was created (see the README).
LAMBDAS = ["order-tracker", "refund-processor"]
REST_API_NAME = "CustomerSupportAPI"
GATEWAY_NAME = "CustomerSupportGateway"
MEMORY_ID_PREFIX = "CustomerSupportMemory-"
KB_NAME = "CustomerSupportKB"
RUNTIME_NAME = "customer_support_agent"
KB_BUCKET = "customer-support-kb-{account}"
DEPLOY_BUCKET = "bedrock-agentcore-codebuild-sources-{account}-{region}"
RUNTIME_LOG_PREFIX = f"/aws/bedrock-agentcore/runtimes/{RUNTIME_NAME}-"


def wait_until(done, what: str, timeout: int = 300) -> bool:
    """Poll `done()` every 10 s; return False if the timeout expires first."""
    start = time.time()
    while not done():
        if time.time() - start > timeout:
            print(f"   ! timed out waiting for {what}; re-run the script later")
            return False
        time.sleep(10)
    return True


class Cleanup:
    def __init__(self, region: str, execute: bool):
        self.region = region
        self.execute = execute
        self.account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
        self.kb = boto3.client("bedrock-agent", region_name=region)
        self.control = boto3.client("bedrock-agentcore-control", region_name=region)
        self.apigw = boto3.client("apigateway", region_name=region)
        self.lambdas = boto3.client("lambda", region_name=region)
        self.iam = boto3.client("iam")
        self.s3 = boto3.client("s3", region_name=region)
        self.logs = boto3.client("logs", region_name=region)
        self.roles: list = []  # role ARNs collected from the resources below

    def act(self, description: str, action) -> None:
        """Print what is (or would be) deleted, and delete it in execute mode.

        AWS errors (e.g. a resource already being deleted) are reported and
        the run continues, so the script can always be re-run.
        """
        print(f"   {'DELETE' if self.execute else 'would delete'}: {description}")
        if self.execute:
            try:
                action()
            except ClientError as error:
                print(f"   ! {error.response['Error']['Code']}: {error.response['Error']['Message']}")

    # ── Knowledge Base ───────────────────────────────────────────────────────
    def knowledge_base(self) -> None:
        print("Knowledge Base")
        summaries = self.kb.list_knowledge_bases()["knowledgeBaseSummaries"]
        kb = next((k for k in summaries if k["name"] == KB_NAME), None)
        if not kb:
            print("   not found")
            return
        kb_id = kb["knowledgeBaseId"]
        role_arn = self.kb.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["roleArn"]

        for ds in self.kb.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]:
            self.act(f"data source {ds['name']} ({ds['dataSourceId']}, status {ds['status']})",
                     lambda ds=ds: self.kb.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"]))
        if self.execute:
            wait_until(lambda: not self.kb.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"],
                       "data sources to be deleted", timeout=900)
        self.act(f"knowledge base {KB_NAME} ({kb_id}, status {kb['status']})",
                 lambda: self.kb.delete_knowledge_base(knowledgeBaseId=kb_id))

        # The KB may still use its service role while it is being deleted, so
        # the role is only queued for deletion once the KB is really gone.
        kb_gone = lambda: not any(k["name"] == KB_NAME for k in self.kb.list_knowledge_bases()["knowledgeBaseSummaries"])
        if not self.execute or wait_until(kb_gone, "the knowledge base to be deleted", timeout=900):
            self.roles.append(role_arn)
        else:
            print(f"   keeping role {role_arn.split('/')[-1]} until the knowledge base is gone")

    # ── AgentCore Gateway ────────────────────────────────────────────────────
    def gateway(self) -> None:
        print("AgentCore Gateway")
        gateway = next((g for g in self.control.list_gateways()["items"] if g["name"] == GATEWAY_NAME), None)
        if not gateway:
            print("   not found")
            return
        gw_id = gateway["gatewayId"]
        self.roles.append(self.control.get_gateway(gatewayIdentifier=gw_id)["roleArn"])

        for target in self.control.list_gateway_targets(gatewayIdentifier=gw_id)["items"]:
            self.act(f"gateway target {target['name']} ({target['targetId']})",
                     lambda t=target: self.control.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"]))
        if self.execute:
            # A gateway cannot be deleted while it still has targets.
            wait_until(lambda: not self.control.list_gateway_targets(gatewayIdentifier=gw_id)["items"],
                       "gateway targets to be deleted")
        self.act(f"gateway {GATEWAY_NAME} ({gw_id})", lambda: self.control.delete_gateway(gatewayIdentifier=gw_id))

    # ── AgentCore Memory ─────────────────────────────────────────────────────
    def memory(self) -> None:
        print("AgentCore Memory")
        memories = [m for m in self.control.list_memories()["memories"] if m["id"].startswith(MEMORY_ID_PREFIX)]
        if not memories:
            print("   not found")
        for m in memories:
            self.act(f"memory {m['id']} (status {m['status']})", lambda m=m: self.control.delete_memory(memoryId=m["id"]))

    # ── API Gateway and Lambdas ──────────────────────────────────────────────
    def rest_api(self) -> None:
        print("API Gateway")
        apis = [a for a in self.apigw.get_rest_apis(limit=500)["items"] if a["name"] == REST_API_NAME]
        if not apis:
            print("   not found")
        for api in apis:
            self.act(f"REST API {REST_API_NAME} ({api['id']})", lambda a=api: self.apigw.delete_rest_api(restApiId=a["id"]))

    def lambda_functions(self) -> None:
        print("Lambda")
        for name in LAMBDAS:
            try:
                config = self.lambdas.get_function_configuration(FunctionName=name)
            except self.lambdas.exceptions.ResourceNotFoundException:
                print(f"   {name}: not found")
                continue
            self.roles.append(config["Role"])
            self.act(f"function {name}", lambda n=name: self.lambdas.delete_function(FunctionName=n))

    # ── IAM roles collected above ────────────────────────────────────────────
    def iam_roles(self) -> None:
        print("IAM roles of the deleted resources")
        if not self.roles:
            print("   none collected")
        for role_arn in dict.fromkeys(self.roles):  # de-duplicated, order kept
            name = role_arn.split("/")[-1]
            try:
                attached = self.iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]
                inline = self.iam.list_role_policies(RoleName=name)["PolicyNames"]
            except self.iam.exceptions.NoSuchEntityException:
                print(f"   {name}: not found")
                continue
            policies = [p["PolicyName"] for p in attached] + [f"{p} (inline)" for p in inline]
            self.act(f"role {name} + policies {policies}", lambda n=name, a=attached, i=inline: self._delete_role(n, a, i))

    def _delete_role(self, name: str, attached: list, inline: list) -> None:
        for policy in attached:
            arn = policy["PolicyArn"]
            self.iam.detach_role_policy(RoleName=name, PolicyArn=arn)
            if ":iam::aws:policy/" in arn:
                continue  # AWS-managed policies are never deleted
            if self.iam.get_policy(PolicyArn=arn)["Policy"]["AttachmentCount"] == 0:
                for version in self.iam.list_policy_versions(PolicyArn=arn)["Versions"]:
                    if not version["IsDefaultVersion"]:
                        self.iam.delete_policy_version(PolicyArn=arn, VersionId=version["VersionId"])
                self.iam.delete_policy(PolicyArn=arn)
        for policy_name in inline:
            self.iam.delete_role_policy(RoleName=name, PolicyName=policy_name)
        self.iam.delete_role(RoleName=name)

    # ── S3 buckets and log groups ────────────────────────────────────────────
    def bucket(self, name: str) -> None:
        try:
            self.s3.head_bucket(Bucket=name)
        except self.s3.exceptions.ClientError:
            print(f"   {name}: not found")
            return
        self.act(f"bucket {name} and all its objects", lambda: self._delete_bucket(name))

    def _delete_bucket(self, name: str) -> None:
        # Covers versioned and unversioned buckets: every version and delete marker.
        for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=name):
            objects = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                       for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
            if objects:
                self.s3.delete_objects(Bucket=name, Delete={"Objects": objects, "Quiet": True})
        self.s3.delete_bucket(Bucket=name)

    def log_groups(self, prefix: str, exact: bool) -> None:
        groups = self.logs.describe_log_groups(logGroupNamePrefix=prefix)["logGroups"]
        groups = [g for g in groups if not exact or g["logGroupName"] == prefix]
        if not groups:
            print(f"   {prefix}{'' if exact else '*'}: not found")
        for g in groups:
            self.act(f"log group {g['logGroupName']}",
                     lambda g=g: self.logs.delete_log_group(logGroupName=g["logGroupName"]))

    def storage_and_logs(self) -> None:
        runtime_left = any(r["agentRuntimeName"] == RUNTIME_NAME
                           for r in self.control.list_agent_runtimes()["agentRuntimes"])
        print("S3 buckets")
        self.bucket(KB_BUCKET.format(account=self.account))
        if runtime_left:
            print(f"   skipping the deployment bucket: runtime {RUNTIME_NAME} still exists (run `agentcore destroy` first)")
        else:
            self.bucket(DEPLOY_BUCKET.format(account=self.account, region=self.region))

        print("CloudWatch log groups")
        for name in LAMBDAS:
            self.log_groups(f"/aws/lambda/{name}", exact=True)
        if runtime_left:
            print("   skipping the runtime log groups: the runtime still exists")
        else:
            self.log_groups(RUNTIME_LOG_PREFIX, exact=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry run)")
    args = parser.parse_args()

    cleanup = Cleanup(main_settings()["REGION"], args.execute)
    print(f"{'EXECUTE' if args.execute else 'DRY RUN'} — account {cleanup.account}, region {cleanup.region}\n")
    # Order matters: dependants first, the IAM roles they use after them.
    cleanup.knowledge_base()
    cleanup.gateway()
    cleanup.memory()
    cleanup.rest_api()
    cleanup.lambda_functions()
    cleanup.iam_roles()
    cleanup.storage_and_logs()
    print("\nDone." if args.execute else "\nDry run only: nothing was deleted. Re-run with --execute to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
