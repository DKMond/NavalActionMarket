from __future__ import annotations

import json
import requests

BASE = "https://storage.googleapis.com/nacleanopenworldprodshards"
SHARD = "cleanopenworldprodeu3"


def fetch(name):
    raw=requests.get(f"{BASE}/{name}_{SHARD}.json",timeout=90).content
    text=raw.decode("utf-8-sig").strip(); eq=text.find("=")
    return json.loads((text if text[:1] in "[{" else text[eq+1:]).strip().rstrip(";"))

shops=fetch("Shops")
items=fetch("ItemTemplates")
print("SHOP_KEYS", list(shops[0].keys()))
for shop in shops:
    if shop.get("ResourcesAdded"):
        print("RESOURCE_SAMPLE_PORT", shop.get("Id"), shop.get("ResourcesAdded")[:3])
        break
for item in items:
    if str(item.get("Id")) in {"2975","1122","1873"}:
        print("ITEM_SAMPLE", item.get("Id"), item.get("Name"), {k:item.get(k) for k in item.keys() if k.lower() in {"weight","itemweight","baseprice","sortinggroup","itemtype","id","name"}})
        print("ITEM_KEYS", item.get("Id"), list(item.keys()))
