"""Delete one actor's long-term memory records.

Long-term memory deliberately outlives sessions, so records extracted from
test conversations show up in every later session of the same actor. Run
this before capturing final evidence.

Short-term events are left alone: the agent only reads long-term records.

Usage (from the repository root):
  uv run --project starter python scripts/purge_memory.py CUST-123 --dry-run
  uv run --project starter python scripts/purge_memory.py CUST-123
"""
import argparse

import boto3
from bedrock_agentcore.memory import MemoryClient

from project_config import main_settings


def actor_namespaces(region: str, memory_id: str, actor_id: str) -> list:
    """Every strategy namespace of the memory, resolved for this actor."""
    strategies = MemoryClient(region_name=region).get_memory_strategies(memory_id)
    return [
        (s.get("namespaceTemplates") or s.get("namespaces"))[0].format(actorId=actor_id)
        for s in strategies
    ]


def list_records(client, memory_id: str, namespace: str) -> list:
    records, token = [], None
    while True:
        kwargs = {"memoryId": memory_id, "namespace": namespace, "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        page = client.list_memory_records(**kwargs)
        records.extend(page.get("memoryRecordSummaries", []))
        token = page.get("nextToken")
        if not token:
            return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("actor_id", help="Actor whose records are deleted, e.g. CUST-123")
    parser.add_argument("--dry-run", action="store_true", help="List the records without deleting them")
    args = parser.parse_args()

    settings = main_settings()
    region, memory_id = settings["REGION"], settings["MEMORY_ID"]
    client = boto3.client("bedrock-agentcore", region_name=region)

    total = 0
    for namespace in actor_namespaces(region, memory_id, args.actor_id):
        records = list_records(client, memory_id, namespace)
        print(f"{namespace}: {len(records)} record(s)")
        for record in records:
            print(f"  - {record['memoryRecordId']}: {record['content'].get('text', '')[:110]!r}")
            if not args.dry_run:
                client.delete_memory_record(memoryId=memory_id, memoryRecordId=record["memoryRecordId"])
        total += len(records)

    print(f"{'Would delete' if args.dry_run else 'Deleted'} {total} record(s) for actor {args.actor_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
