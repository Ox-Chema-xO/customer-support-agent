# Test transcript — Customer Support Agent on Amazon Bedrock AgentCore

- Runtime: `arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc`
- Region: `us-east-1` · Memory: `CustomerSupportMemory-SfF8oiHegs` · Customer: `CUST-123`
- Generated: 2026-11-01 02:56 UTC
- Long-term memory records for CUST-123 before the run: 0
- Every invocation uses a fresh runtime session (`--session-id`) and passes `customer_id`.

## Test 1 — Order Tracking

Expected (README): shipping status, tracking number TRK987654321, carrier UPS, estimated delivery


```bash
agentcore invoke --session-id e9692c27-7711-4233-8055-3bea14a758c8 '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-123", "session_id": "t1"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: e9692c27-7711-4233-8055-3bea14a758c8                                                                                                                │
│ Request ID: 8ddb4e3f-8a3f-4764-abb2-8fd7b10ace40                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
I've found your order ORD-001. Here are the details:

**Order Status:** SHIPPED  
**Items:**  
- Wireless Headphones Pro (1 x $89.99)

**Total:** $89.99  
**Carrier:** UPS  
**Tracking Number:** TRK987654321  
**Estimated Delivery:** November 3, 2026

Your order is currently in transit and expected to be delivered by November 3rd. You can track the package using the tracking number TRK987654321 with UPS.
```
Agent log lines from CloudWatch (runtime session `e9692c27-7711-4233-8055-3bea14a758c8`):
```
Invocation | actor=CUST-123 session=t1
Gateway tools loaded: 6
Memory | retrieved 0 records | actor=CUST-123
Tool call | order-tracker___get_order input={"order_id": "ORD-001"} status=success result={"order_id":"ORD-001","customer_id":"CUST-123","status":"SHIPPED","items":[{"name":"Wireless Headphones Pro","qty":1,"price":89.99}],"total":89.99,"tracking_number":"TRK987654321","carrier":"UPS","estimated_delivery":"2026-11-03"}
Memory | saved turn | actor=CUST-123 session=t1
```

## Test 2 — Refund Processing

Expected (README): refund ID, APPROVED status, 3-5 business days message


```bash
agentcore invoke --session-id 60d4089c-b770-468f-9327-8b08045cd21a '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "customer_id": "CUST-123", "session_id": "t2"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: 60d4089c-b770-468f-9327-8b08045cd21a                                                                                                                │
│ Request ID: d4c38e93-a846-46e7-a4c7-b1ddd6c4174a                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
Your refund for the Kindle Paperwhite (ORD-002) has been approved. The amount of $139.99 will be credited to your account within 3-5 business days.

Your refund ID is **REF-H64W1FR2**.
```
Agent log lines from CloudWatch (runtime session `60d4089c-b770-468f-9327-8b08045cd21a`):
```
Invocation | actor=CUST-123 session=t2
Gateway tools loaded: 6
Memory | retrieved 0 records | actor=CUST-123
Tool call | order-tracker___get_order input={"order_id": "ORD-002"} status=success result={"order_id":"ORD-002","customer_id":"CUST-123","status":"DELIVERED","items":[{"name":"Kindle Paperwhite","qty":1,"price":139.99}],"total":139.99,"tracking_number":"TRK123456789","carrier":"USPS","delivered_date":"2026-10-29"}
Tool call | refund-processor___initiate_refund input={"reason": "Return of Kindle Paperwhite", "order_id": "ORD-002", "amount": 139.99} status=success result={"statusCode":200,"body":"{\"refund_id\": \"REF-H64W1FR2\", \"order_id\": \"ORD-002\", \"status\": \"APPROVED\", \"amount\": 139.99, \"message\": \"Refund approved. Credit appears in 3-5 business days.\", \"created_at\": \"2026-11-01T02:57:08.792614+00:00\"}"}
Memory | saved turn | actor=CUST-123 session=t2
```

## Test 3 — Knowledge Base (RAG)

Expected (README): free same-day shipping, 15% discount, priority support


```bash
agentcore invoke --session-id f0227e66-b723-42a1-bf8c-6c08b9543a29 '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: f0227e66-b723-42a1-bf8c-6c08b9543a29                                                                                                                │
│ Request ID: 14a010d2-fc92-4950-86d2-8f54bf5eebe3                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
The **Platinum loyalty tier** offers these exclusive benefits:

- **Free same-day shipping** on eligible orders
- **15% discount** on purchases
- **Priority customer support** for faster assistance

You reach Platinum status when you earn **5,000 or more loyalty points**. Points are earned at different rates depending on what you purchase:
- Standard items: 1 point per $1 spent  
- Amazon devices: 2 points per $1 spent
- Amazon Fresh: 5 points per $1 spent

You can redeem points at a rate of **100 points = $1 discount**, with a minimum redemption of 500 points. Points stay valid for **2 years** from the date 
they're earned.
```
Agent log lines from CloudWatch (runtime session `f0227e66-b723-42a1-bf8c-6c08b9543a29`):
```
Invocation | actor=CUST-123 session=t3
Gateway tools loaded: 6
Memory | retrieved 0 records | actor=CUST-123
KB search | query='benefits of Platinum loyalty tier' chunks=2
Tool call | search_knowledge_base input={"query": "benefits of Platinum loyalty tier"} status=success result=- Gold: 1000 to 4999 points
Memory | saved turn | actor=CUST-123 session=t3
```

## Test 4 — Long-Term Memory

Expected (README): agent recalls "Jane" and "concise responses" in a new session

**Session A (s-A) — introduce yourself**
```bash
agentcore invoke --session-id d3684ad1-6fd1-45cd-ba37-d4339ce21769 '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-123", "session_id": "s-A"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: d3684ad1-6fd1-45cd-ba37-d4339ce21769                                                                                                                │
│ Request ID: be6c7730-4c19-431c-9715-914b712952ef                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
Hello Jane! I understand you prefer concise responses. How can I assist you today?
```
Agent log lines from CloudWatch (runtime session `d3684ad1-6fd1-45cd-ba37-d4339ce21769`):
```
Invocation | actor=CUST-123 session=s-A
Gateway tools loaded: 6
Memory | retrieved 0 records | actor=CUST-123
Memory | saved turn | actor=CUST-123 session=s-A
```

Waited 63s for long-term memory extraction. New records for CUST-123:
```
The user's name is Jane.
The user has an order ORD-001 for Wireless Headphones Pro (1 × $89.99) shipped via UPS with tracking number TRK987654321, with an estimated delivery of November 3, 2026.
The user prefers concise responses.
The user requested a return and refund for a Kindle Paperwhite (Order ORD-002) for $139.99, with refund ID REF-H64W1FR2, approved on 2026-11-01.
{"context":"General assistant interaction","preference":"Prefers concise responses","categories":["communication","response style"]}
```

**Session B (s-B) — new session, same customer**
```bash
agentcore invoke --session-id 41980127-a084-458a-915c-bf68d05b5783 '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-123", "session_id": "s-B"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: 41980127-a084-458a-915c-bf68d05b5783                                                                                                                │
│ Request ID: 17e9d6be-6503-43b7-8ade-5a6fcffada28                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
Yes, Jane. I remember your name and that you prefer concise responses. How can I assist you today?
```
Agent log lines from CloudWatch (runtime session `41980127-a084-458a-915c-bf68d05b5783`):
```
Invocation | actor=CUST-123 session=s-B
Gateway tools loaded: 6
Memory | retrieved 5 records | actor=CUST-123
Memory | saved turn | actor=CUST-123 session=s-B
```

## Test 5 — Loyalty Discount Calculation

Expected (README): points redeemed, tier discount 10%, final total, remaining points


```bash
agentcore invoke --session-id 28ff6b20-5ca8-468e-9188-948345dcd4b4 '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "customer_id": "CUST-123", "session_id": "t5"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: 28ff6b20-5ca8-468e-9188-948345dcd4b4                                                                                                                │
│ Request ID: ef9667d3-ff3a-42fd-b952-fe6434ada500                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
Great news, Jane! As a Gold member, your $150 standard order qualifies for **$51 in total savings**:  

- **$40 discount** from redeeming 4,000 points  
- **$11 discount** from your Gold tier (10%)  

Your **final total** will be **$99**.  
You’ll also earn **99 points** on this purchase, leaving you with **349 points** remaining.
```
Agent log lines from CloudWatch (runtime session `28ff6b20-5ca8-468e-9188-948345dcd4b4`):
```
Invocation | actor=CUST-123 session=t5
Gateway tools loaded: 6
Memory | retrieved 5 records | actor=CUST-123
Code Interpreter | discount calculated | final_total=99.0
Tool call | calculate_loyalty_discount input={"order_total": 150.0, "loyalty_points": 4250, "tier": "Gold", "product_category": "standard"} status=success result={"tier": "Gold", "product_category": "standard", "order_total": 150.0, "points_redeemed": 4000, "points_discount": 40.0, "subtotal_after_points": 110.0, "tier_discount_pct": 10.0, "tier_discount": 11.0, "final_total": 99.0, "total_savings": 51.0, "points_earned": 99, "remaining_points": 349, "calcul
Memory | saved turn | actor=CUST-123 session=t5
```

## Test 6 — Browser Tool

Expected (README): page title retrieved from live Udacity.com


```bash
agentcore invoke --session-id 0d37fc44-ef6f-49c8-9c14-35c0ad01b569 '{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": "CUST-123", "session_id": "t6"}'
```
Terminal output:
```
╭─────────────────────────────────────────────────────────────────── customer_support_agent ───────────────────────────────────────────────────────────────────╮
│ Session: 0d37fc44-ef6f-49c8-9c14-35c0ad01b569                                                                                                                │
│ Request ID: 1a5e8396-333a-472c-8a1c-0f901a0f00f7                                                                                                             │
│ ARN: arn:aws:bedrock-agentcore:us-east-1:581851817977:runtime/customer_support_agent-Sb19lW28Qc                                                              │
│ Logs: aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --follow   │
│       aws logs tail /aws/bedrock-agentcore/runtimes/customer_support_agent-Sb19lW28Qc-DEFAULT --log-stream-name-prefix "2026/11/01/[runtime-logs" --since 1h │
│ GenAI Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

Response:
The page title for https://www.udacity.com is:

**"Learn the Latest Tech Skills; Advance Your Career | Udacity"**
```
Agent log lines from CloudWatch (runtime session `0d37fc44-ef6f-49c8-9c14-35c0ad01b569`):
```
Invocation | actor=CUST-123 session=t6
Gateway tools loaded: 6
Memory | retrieved 5 records | actor=CUST-123
Tool call | browser input={"browser_input": {"action": {"type": "init_session", "description": "Session for getting page title", "session_name": "title-check-session"}}} status=success result=
Tool call | browser input={"browser_input": {"action": {"type": "navigate", "url": "https://www.udacity.com", "session_name": "title-check-session"}}} status=error result=Error: Timeout 30000ms exceeded.
Tool call | browser input={"browser_input": {"action": {"type": "evaluate", "session_name": "title-check-session", "script": "document.title;"}}} status=success result=Evaluation result: Learn the Latest Tech Skills; Advance Your Career | Udacity
Memory | saved turn | actor=CUST-123 session=t6
```

