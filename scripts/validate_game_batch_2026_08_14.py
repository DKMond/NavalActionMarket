from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from naval_action_market.providers import NavalGamingHTMLProvider

BASE = "https://storage.googleapis.com/nacleanopenworldprodshards"
SHARD = "cleanopenworldprodeu3"
OUT = Path("research/game_batch_2026_08_14.json")

# User-provided screenshots received 2026-08-14 around 16:41 Europe/Bucharest.
# UI semantics: Sell = player sells to port; Buy = player buys from port.
OBS: dict[int, dict[str, Any]] = {
    150: {"port": "Aves", "items": {
        "Madagascar Jewels": (1000,None,1083,29), "Gold ingots": (1000,None,1062,10),
        "Chinese tea": (1000,None,1091,10), "Flannel": (4,None,552,8),
        "European books": (1000,None,1064,4), "Pompano": (1,None,1,46), "Flounder": (1,None,1,22),
        "Ball Ammo": (1,None,3,4284), "Rig Repairs": (1,None,266,1081),
        "Chain Ammo": (1,None,15,11444), "Hull Repairs": (1,None,200,647),
        "Medicine": (1,None,60,196), "Grape Ammo": (1,None,57,19600), "Gunpowder": (1,None,3,300000),
    }},
    23: {"port": "Pitt's Town", "items": {
        "Gunpowder": (1,None,3,45732), "Medicine": (7,1436,29,8537), "Hull Repairs": (22,526,120,3739),
        "Rig Repairs": (22,471,120,4175), "Chain Ammo": (5,1871,33,17355), "Grape Ammo": (5,1615,49,2707),
        "Ball Ammo": (2,15669,5,9356), "Swedish Iron": (124,None,249,12549),
        "Norwegian Fox Fur": (259,None,518,4162), "Swedish flax": (8,None,16,54943),
        "Akvavit": (124,None,249,12729), "Russian Vodka": (124,None,249,12549),
        "Danish Beer": (259,None,518,4232), "Danish Pickled Herring": (259,None,518,4232),
        "Iron Ore": (6,4986,15,10000), "Lignum Vitae Log": (6,1158,49,5517), "Oak Log": (3,5395,10,5723),
        "Live Oak Log": (186,2000,15,None), "Pompano": (2,1465,100,1700), "Tuna": (665,123,1,None),
        "Dorado": (385,121,3000,100),
    }},
    112: {"port": "La Mona", "items": {
        "Rig Repairs": (1,None,80,3860), "Chain Ammo": (1,None,12,15370), "Ball Ammo": (1,None,4,15000),
        "Hull Repairs": (1,None,100,4068), "Gunpowder": (1,None,3,45000), "Medicine": (1,None,20,3705),
        "Grape Ammo": (1,None,12,18326), "Molasses": (1,None,4,2400060), "Teak Log": (1,None,10,1808097),
        "Stone Block": (1,None,3,1200000), "Fine Leather": (259,None,518,4527), "Fishing Hooks": (1,None,4,829),
        "Fishing Nets": (1,None,4,802), "Saltpeter": (1,None,6,1200000), "White Oak Log": (1,None,10,6),
        "Medicinal bark": (1,None,10,12), "Lignum Vitae Log": (1,None,6,6), "Fish Meat": (1,None,4,146),
        "Oak Log": (1,None,4,3), "Fir Log": (1,None,4,1), "Topaz": (1000,None,1055,3),
    }},
    140: {"port": "Saint John's", "items": {
        "Teak Log": (1,None,10,1645641), "Iron Ore": (1,None,7,3600000), "Fishing Nets": (1,None,4,422),
        "Fishing Hooks": (1,None,4,526), "Black Ironwood": (863,None,1727,1530), "Salt": (1,None,4,213),
        "Oak Log": (1,None,4,1), "Grape Ammo": (1,None,57,17), "Ball Ammo": (1,None,None,None),
        "Hull Repairs": (1,None,None,None), "Rig Repairs": (1,None,None,None), "Gunpowder": (1,None,None,None),
        "Medicine": (1,None,None,None), "Chain Ammo": (1,None,None,None),
    }},
}

ALIASES = {
    "Swedish Iron": ["Swedish iron"], "Swedish flax": ["Swedish Flax"], "Medicinal bark": ["Medicinal Bark"],
    "Gold ingots": ["Gold Ingots"], "Chinese tea": ["Chinese Tea"], "European books": ["European Books"],
}

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def parse(raw: bytes):
    s=raw.decode("utf-8-sig").strip(); eq=s.find("=")
    return json.loads((s if s[:1] in "[{" else s[eq+1:]).strip().rstrip(";"))

def fetch(name):
    u=f"{BASE}/{name}_{SHARD}.json"; r=requests.get(u,timeout=90,headers={"User-Agent":"NavalActionMarket-ground-truth/1.0"}); r.raise_for_status()
    return parse(r.content), {"url":u,"etag":r.headers.get("ETag"),"lastModified":r.headers.get("Last-Modified"),"generation":r.headers.get("x-goog-generation")}

def norm(s): return re.sub(r"[^a-z0-9]+"," ",(s or "").casefold()).strip()

def resolve(items,name):
    wants={norm(name),*(norm(x) for x in ALIASES.get(name,[]))}
    for x in items:
        if norm(x.get("Name")) in wants: return x
    return None

def buy_qty(r):
    # When a buy-side player contract exists, the game exposes the contract quantity,
    # even when the underlying server Quantity is also populated.
    q=r.get("BuyContractQuantity")
    if r.get("IsContractsExist") and q not in (None,-1): return q
    if r.get("Quantity") == -1: return None if q == -1 else q
    return r.get("Quantity")

def sell_qty(r):
    q=r.get("SellContractQuantity"); return None if q == -1 else q

def same(a,b): return a == b

def main():
    started=now(); ports,pm=fetch("Ports"); shops,sm=fetch("Shops"); items,im=fetch("ItemTemplates")
    pby={int(x["Id"]):x for x in ports}; sby={int(x["Id"]):x for x in shops}
    ngp=NavalGamingHTMLProvider(batch_pause=0)
    rows=[]; counts=Counter(); by_port=defaultdict(Counter)
    for pid,spec in OBS.items():
        port=pby[pid]; shop=sby[pid]; reg={int(x["TemplateId"]):x for x in shop.get("RegularItems",[]) or []}
        requested={"id":pid,"name":spec["port"],"nation":port.get("Nation"),"nationName":None,"x":None,"y":None}
        html=ngp._get(f"/trading/market/?portid={pid}&server=main"); ng=ngp.parse_port_page(html,requested)
        ngm={int(x["itemId"]):x for x in ng.get("marketContracts",[])}; ngg={int(x["itemId"]):x for x in ng.get("tradeGoods",[])}
        for name,(gsp,gsq,gbp,gbq) in spec["items"].items():
            counts["observations"]+=1; by_port[spec["port"]]["observations"]+=1
            it=resolve(items,name)
            out={"portId":pid,"port":spec["port"],"item":name,"game":{"sellPrice":gsp,"sellQty":gsq,"buyPrice":gbp,"buyQty":gbq}}
            if not it:
                out["status"]="ITEM_UNRESOLVED"; counts["itemUnresolved"]+=1; rows.append(out); continue
            iid=int(it["Id"]); out.update({"itemId":iid,"apiName":it.get("Name"),"sortingGroup":it.get("SortingGroup")})
            raw=reg.get(iid); ng_market=ngm.get(iid); ng_good=ngg.get(iid)
            out["navalGamingMarket"]=ng_market; out["navalGamingTradeGood"]=ng_good

            # Score near-live NavalGaming against the screenshot whenever it exposes that row.
            if ng_market:
                ng_matches={"buyPrice":same(ng_market.get("supplyPrice"),gbp),"buyQty":same(ng_market.get("supplyQty"),gbq),"sellPrice":same(ng_market.get("demandPrice"),gsp if gsq is not None else None),"sellQty":same(ng_market.get("demandQty"),gsq)}
                out["navalGamingVsGame"]=ng_matches
                for k,v in ng_matches.items(): counts["ng_"+k+"Match"]+=int(v)
            elif ng_good:
                ng_matches={"buyPrice":same(ng_good.get("price"),gbp),"buyQty":same(ng_good.get("qty"),gbq)}
                out["navalGamingVsGame"]=ng_matches
                for k,v in ng_matches.items(): counts["ng_"+k+"Match"]+=int(v)

            if not raw:
                out["status"]="API_ABSENT_AT_RESET"; counts["apiAbsentAtReset"]+=1; by_port[spec["port"]]["apiAbsentAtReset"]+=1; rows.append(out); continue
            api={"BuyPrice":raw.get("BuyPrice"),"SellPrice":raw.get("SellPrice"),"Quantity":raw.get("Quantity"),"BuyContractQuantity":raw.get("BuyContractQuantity"),"SellContractQuantity":raw.get("SellContractQuantity"),"IsContractsExist":raw.get("IsContractsExist"),"derivedBuyQty":buy_qty(raw),"derivedSellQty":sell_qty(raw)}
            out["api"]=api
            m={"buyPrice":same(api["BuyPrice"],gbp),"sellPrice":same(api["SellPrice"],gsp),"buyQty":same(api["derivedBuyQty"],gbq),"sellQty":same(api["derivedSellQty"],gsq)}
            out["apiVsGame"]=m
            for k,v in m.items(): counts["api_"+k+"Match"]+=int(v); by_port[spec["port"]]["api_"+k+"Match"]+=int(v)
            if all(m.values()): st="API_RESET_MATCH"
            elif m["buyPrice"] and m["sellPrice"]: st="API_TEMPORAL_QTY_DIVERGENCE"
            else: st="API_TEMPORAL_PRICE_OR_STATE_DIVERGENCE"
            out["status"]=st; counts[st]+=1; by_port[spec["port"]][st]+=1; rows.append(out)
    report={"generatedAt":now(),"startedAt":started,"observationBatch":{"receivedApprox":"2026-08-14T16:41:00+03:00","source":"10 user-provided in-game screenshots","note":"API Shops generation is the daily reset snapshot; NavalGaming pages were fetched during validation and are the closer-time comparator."},"api":{"shard":SHARD,"ports":pm,"shops":sm,"items":im},"summary":dict(counts),"byPort":{k:dict(v) for k,v in by_port.items()},"rows":rows}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"report":str(OUT),"summary":dict(counts),"byPort":report["byPort"]},indent=2,ensure_ascii=False))

if __name__=="__main__": main()
