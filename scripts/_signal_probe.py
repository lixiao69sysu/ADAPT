import json
from agent.memory.signals import SignalParser
order = {"scenario": "travel_ticket", "merchant_name": "12306", "tags": ["动车", "二等座"],
         "items": [{"product_name": "D2372 成都东-黄山北 二等座 8月5日", "price": 498, "quantity": 1}]}
inter = {"date": "2022-07-23", "behavior": [{"behavior_type": "order", "content": order}]}
parser = SignalParser()
sigs = parser.parse([inter])
for s in sigs:
    print(f"{s.type:24} obj={s.object[:40]!r} conf={s.confidence}")
print("--- direct extractor ---")
print([s.object for s in parser._extract_order_class_tags(order, "ts", 6.0)])
print("tags dug:", parser._dig_list(order, "tags", "tag"))
