"""Download once, validate before atomic save, reuse the last good OSM snapshot."""
import gzip
import json
import os
import tempfile
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

from astar_routing import ROAD_TYPES, RoutingError, build_graph
from locations import CITY_RING_SNAPSHOT

BASE = Path(__file__).resolve().parent
SNAPSHOT = BASE / "data" / "nasr_city_roads_v1.json.gz"
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter")


def road_query():
    # A margin permits real detours at the project boundary instead of cutting
    # a valid road at the city's polygon. Complete ways and restrictions follow.
    south=min(p[0] for p in CITY_RING_SNAPSHOT)-0.015
    north=max(p[0] for p in CITY_RING_SNAPSHOT)+0.015
    west=min(p[1] for p in CITY_RING_SNAPSHOT)-0.018
    east=max(p[1] for p in CITY_RING_SNAPSHOT)+0.018
    classes="|".join(sorted(ROAD_TYPES))
    return f'''[out:json][timeout:25];
way["highway"~"^({classes})$"]({south},{west},{north},{east})->.roads;
rel(bw.roads)["type"~"^restriction"]->.turns;
(.roads;.turns;);(._;>;);out body;'''


def read_snapshot(path=SNAPSHOT):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("savebite_schema") != 1:
            raise ValueError("schema")
        graph = build_graph(data)
        return data, graph
    except (OSError, EOFError, ValueError, TypeError, KeyError, zlib.error) as error:
        raise RoutingError("نسخة شبكة الشوارع غير متاحة أو تالفة. حمّل الشبكة أو أعد المحاولة.") from error


def download_snapshot(path=SNAPSHOT):
    last_error = None
    for endpoint in ENDPOINTS:
        try:
            request = urllib.request.Request(endpoint,
                data=urllib.parse.urlencode({"data": road_query()}).encode(),
                headers={"User-Agent": "SaveBite-University-Project/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=35) as response:
                data=json.load(response)
            graph=build_graph(data)
            if graph.metadata["nodes"] < 1000:
                raise RoutingError("تحميل شبكة الشوارع غير مكتمل.")
            data["savebite_schema"]=1
            data["downloaded_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
            data["source_url"]=endpoint
            path=Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix="roads-",suffix=".gz",dir=path.parent)
            os.close(descriptor)
            try:
                with gzip.open(temporary,"wt",encoding="utf-8") as handle:
                    json.dump(data,handle,ensure_ascii=False,separators=(",",":"))
                os.replace(temporary,path)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
            return data, graph
        except (OSError, ValueError, TypeError, KeyError) as error:
            last_error=error
    raise RoutingError("تعذر تحميل شبكة الشوارع من OpenStreetMap حاليًا. تأكد من الإنترنت واضغط إعادة المحاولة؛ الحجوزات لم تتغير.") from last_error


def load_road_graph():
    if SNAPSHOT.exists():
        try:return read_snapshot()
        except RoutingError:pass
    return download_snapshot()
