"""Hand-written A* on a directed, conservatively filtered OSM road graph.

g(n): accumulated road distance in metres. h(n): great-circle distance to goal.
Every edge weight is that same spherical distance between adjacent OSM nodes;
the triangle inequality makes h admissible and consistent. The search state
also contains the incoming way and previous node to enforce mapped turns.
"""
import heapq
import itertools
import math
from dataclasses import dataclass

EARTH_M = 6371008.8
ROAD_TYPES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
              "residential", "living_street", "service", "motorway_link", "trunk_link",
              "primary_link", "secondary_link", "tertiary_link"}
ACCESS = {"yes", "permissive", "designated"}
MODES = ("motorcar", "motor_vehicle", "vehicle", "access")
CONDITIONAL_PREFIXES = ("access", "motorcar", "motor_vehicle", "vehicle", "oneway")
TURN_TYPES = {"no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn",
              "no_entry", "no_exit", "only_left_turn", "only_right_turn",
              "only_straight_on", "only_u_turn"}


class RoutingError(ValueError):
    pass


def coordinate(point):
    if not isinstance(point, (tuple, list)) or len(point) != 2 or any(isinstance(v, bool) for v in point):
        raise RoutingError("إحداثيات الاستلام أو الجهة غير صحيحة.")
    try:
        lat, lon = map(float, point)
    except (TypeError, ValueError, OverflowError):
        raise RoutingError("إحداثيات الاستلام أو الجهة غير صحيحة.") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise RoutingError("إحداثيات الاستلام أو الجهة غير صحيحة.")
    return lat, lon


def metres(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    d = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return EARTH_M*2*math.asin(math.sqrt(min(1.0, max(0.0, d))))


def access_allowed(tags, direction=None):
    for mode in MODES:
        if direction and mode+":"+direction in tags:
            return tags[mode+":"+direction] in ACCESS
        if mode in tags:
            return tags[mode] in ACCESS
    return True


def conditional(tags):
    return any(":conditional" in k and k.split(":")[0] in CONDITIONAL_PREFIXES for k in tags)


@dataclass(frozen=True)
class Edge:
    to: int
    way: int
    length: float
    name: str


@dataclass
class RoadGraph:
    nodes: dict
    adjacency: dict
    rules: dict
    metadata: dict

    def turn_allowed(self, previous, at, incoming, outgoing):
        if incoming is None:
            return True
        relevant = self.rules.get((at, incoming), ())
        only = []
        for target_way, restriction in relevant:
            is_target = outgoing.way == target_way
            # Same-way U-turn vs continuing on the same way are distinct.
            if is_target and target_way == incoming:
                if restriction.endswith("u_turn"):
                    is_target = outgoing.to == previous
                elif restriction.endswith("straight_on"):
                    is_target = outgoing.to != previous
            if restriction.startswith("only_"):
                only.append(is_target)
            elif is_target:
                return False
        return not only or any(only)


def build_graph(osm):
    if not isinstance(osm, dict):
        raise RoutingError("بيانات شبكة الشوارع غير صالحة.")
    elements = osm.get("elements")
    if not isinstance(elements, list) or osm.get("remark"):
        raise RoutingError("بيانات شبكة الشوارع ناقصة أو غير صالحة.")
    for element in elements:
        if (not isinstance(element, dict) or type(element.get("id")) is not int
                or not isinstance(element.get("tags", {}), dict)):
            raise RoutingError("شكل بيانات الشوارع غير صالح.")
        if element.get("type") == "way" and (not isinstance(element.get("nodes"), list)
                or any(type(n) is not int for n in element["nodes"])):
            raise RoutingError("بيانات مقاطع الشوارع غير صالحة.")
        if element.get("type") == "relation" and (not isinstance(element.get("members"), list)
                or any(not isinstance(m, dict) or type(m.get("ref")) is not int for m in element["members"])):
            raise RoutingError("بيانات قيود الدوران غير صالحة.")
    if not isinstance(osm.get("osm3s", {}), dict):
        raise RoutingError("بيانات مصدر الشوارع غير صالحة.")
    raw_nodes = {e["id"]: e for e in elements if e.get("type") == "node"}
    ways = {e["id"]: e for e in elements if e.get("type") == "way"}
    blocked_nodes, blocked_ways, rules = set(), set(), {}
    conservative = 0
    for node_id, node in raw_nodes.items():
        tags = node.get("tags", {})
        if (not access_allowed(tags) or conditional(tags)
                or tags.get("barrier", "no") != "no" or tags.get("ford") == "yes"):
            blocked_nodes.add(node_id)
    for relation in elements:
        if relation.get("type") != "relation":
            continue
        tags = relation.get("tags", {})
        if not tags.get("type", "").startswith("restriction"):
            continue
        except_modes = {s.strip() for s in tags.get("except", "").split(";")}
        if except_modes.intersection({"motorcar", "motor_vehicle", "vehicle"}):
            continue
        restriction = tags.get("restriction:motorcar", tags.get("restriction:motor_vehicle", tags.get("restriction:vehicle", tags.get("restriction", ""))))
        # Relations that apply only to another mode do not restrict motorcars.
        if not restriction and not any(k.startswith("restriction:conditional") or k.startswith("restriction:motorcar:conditional") or k.startswith("restriction:motor_vehicle:conditional") for k in tags):
            continue
        members = relation.get("members", [])
        sources = [m["ref"] for m in members if m.get("role") == "from" and m.get("type") == "way"]
        targets = [m["ref"] for m in members if m.get("role") == "to" and m.get("type") == "way"]
        via = [m for m in members if m.get("role") == "via"]
        unsupported = (restriction not in TURN_TYPES or any(":conditional" in k for k in tags)
            or len(via) != 1 or via[0].get("type") != "node" or not sources or not targets)
        if unsupported:
            conservative += 1
            blocked_ways.update(m["ref"] for m in members if m.get("type") == "way")
            blocked_nodes.update(m["ref"] for m in via if m.get("type") == "node")
            continue
        for source in sources:
            rules.setdefault((via[0]["ref"], source), []).extend((target, restriction) for target in targets)
    coords = {}
    for key, node in raw_nodes.items():
        coords[key] = coordinate((node.get("lat"), node.get("lon")))
    adjacency = {}
    excluded = 0
    for way_id, way in ways.items():
        tags = way.get("tags", {})
        if (way_id in blocked_ways or tags.get("highway") not in ROAD_TYPES
                or not access_allowed(tags) or conditional(tags)
                or tags.get("area") == "yes" or tags.get("ford") == "yes"):
            excluded += 1
            continue
        direction = tags.get("oneway:motorcar", tags.get("oneway:motor_vehicle", tags.get("oneway:vehicle", tags.get("oneway", ""))))
        if not direction and tags.get("highway") == "motorway_link":
            # This class does not unambiguously imply one-way in OSM. Do not
            # guess an untagged ramp's permitted direction.
            excluded += 1
            continue
        if not direction:
            direction = "yes" if tags.get("junction") in {"roundabout", "circular"} or tags.get("highway") == "motorway" else "no"
        if direction not in {"yes", "1", "true", "-1", "no", "0", "false"}:
            excluded += 1
            continue
        forward = direction != "-1" and access_allowed(tags, "forward")
        backward = direction in {"-1", "no", "0", "false"} and access_allowed(tags, "backward")
        node_ids = way.get("nodes", [])
        if any(n not in coords for n in node_ids):
            raise RoutingError("شبكة الشوارع ناقصة: في عقد مفقودة.")
        name = tags.get("name:ar") or tags.get("name") or tags.get("ref") or "شارع بدون اسم مسجل"
        for u, v in zip(node_ids, node_ids[1:]):
            if u in blocked_nodes or v in blocked_nodes or u == v:
                continue
            length = metres(coords[u], coords[v])
            if forward:
                adjacency.setdefault(u, []).append(Edge(v, way_id, length, name))
            if backward:
                adjacency.setdefault(v, []).append(Edge(u, way_id, length, name))
    used = set(adjacency)
    used.update(e.to for edges in adjacency.values() for e in edges)
    if not used:
        raise RoutingError("شبكة الشوارع لا تحتوي طرقًا متاحة.")
    metadata = {"nodes": len(used), "edges": sum(map(len, adjacency.values())),
        "restrictions": sum(map(len, rules.values())), "conservative_restrictions": conservative,
        "excluded_ways": excluded, "osm_timestamp": osm.get("osm3s", {}).get("timestamp_osm_base", "غير مسجل")}
    return RoadGraph({n: coords[n] for n in used}, adjacency, rules, metadata)


def nearest_node(graph, point, max_distance=250):
    point = coordinate(point)
    node = min(graph.nodes, key=lambda n: metres(point, graph.nodes[n]))
    distance = metres(point, graph.nodes[node])
    if distance > max_distance:
        raise RoutingError(f"الموقع بعيد عن شبكة الشوارع المتاحة ({distance:.0f} متر). راجع نقطة الوصول؛ لم يُرسم مسار بديل وهمي.")
    return node, distance


def astar(graph, start, goal):
    """A* with re-opening and exact incoming-edge state; never mutates the graph."""
    if start not in graph.nodes or goal not in graph.nodes:
        raise RoutingError("نقطة البداية أو النهاية مش موجودة في شبكة الشوارع.")
    initial = (None, start, None)
    queue, serial = [], itertools.count()
    distances, parents = {initial: 0.0}, {}
    heapq.heappush(queue, (metres(graph.nodes[start], graph.nodes[goal]), 0.0, next(serial), initial))
    expanded = 0
    while queue:
        _, cost, _, state = heapq.heappop(queue)
        if cost != distances.get(state):
            continue
        expanded += 1
        previous, node, incoming = state
        if node == goal:
            edges, nodes = [], [node]
            while state in parents:
                state, edge = parents[state]
                edges.append(edge)
                nodes.append(state[1])
            return {"nodes": nodes[::-1], "edges": edges[::-1], "distance_m": cost, "expanded": expanded}
        for edge in graph.adjacency.get(node, ()):
            if not graph.turn_allowed(previous, node, incoming, edge):
                continue
            if not math.isfinite(edge.length) or edge.length < 0:
                raise RoutingError("وزن طريق غير صالح؛ تم إيقاف الحساب.")
            next_state = (node, edge.to, edge.way)
            candidate = cost + edge.length
            if candidate < distances.get(next_state, math.inf):
                distances[next_state] = candidate
                parents[next_state] = (state, edge)
                estimate = candidate + metres(graph.nodes[edge.to], graph.nodes[goal])
                heapq.heappush(queue, (estimate, candidate, next(serial), next_state))
    raise RoutingError("مفيش طريق متصل ومسموح في الشبكة الحالية بين النقطتين. لم يتم استبداله بخط مستقيم.")


def route_points(graph, origin, destination):
    origin, destination = coordinate(origin), coordinate(destination)
    start, start_gap = nearest_node(graph, origin)
    goal, goal_gap = nearest_node(graph, destination)
    result = astar(graph, start, goal)
    result.update({"coordinates": [graph.nodes[n] for n in result["nodes"]],
        "origin_gap_m": start_gap, "destination_gap_m": goal_gap,
        "origin": origin, "destination": destination})
    return result
