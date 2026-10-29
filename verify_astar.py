"""Portable, offline verification: python verify_astar.py. No third-party packages."""
import heapq
import math
import random
from astar_routing import astar, build_graph, RoutingError, metres


def dijkstra(graph, start, goal):
    """Independent reference for directed graphs without turn restrictions."""
    distances, queue = {start: 0.0}, [(0.0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distances[node]:
            continue
        if node == goal:
            return cost
        for edge in graph.adjacency.get(node, []):
            candidate = cost+edge.length
            if candidate < distances.get(edge.to, math.inf):
                distances[edge.to] = candidate
                heapq.heappush(queue, (candidate, edge.to))
    return math.inf


def run():
    rng = random.Random(2026)
    reachable = unreachable = 0
    for _ in range(20):
        elements = [dict(type="node", id=i, lat=30.04+rng.random()*.04,
                         lon=31.33+rng.random()*.04) for i in range(1, 16)]
        key = 1
        for u in range(1, 16):
            for v in range(1, 16):
                if u != v and rng.random() < .10:
                    elements.append(dict(type="way", id=key, nodes=[u, v],
                        tags={"highway": "residential", "oneway": "yes"}))
                    key += 1
        graph = build_graph({"elements": elements})
        for _ in range(20):
            start, goal = rng.choices(sorted(graph.nodes), k=2)
            expected = dijkstra(graph, start, goal)
            try:
                result = astar(graph, start, goal)
            except RoutingError:
                assert expected == math.inf, "A* missed a reachable goal"
                unreachable += 1
                continue
            assert math.isclose(result["distance_m"], expected, abs_tol=1e-6), "Non-optimal result"
            assert math.isclose(sum(e.length for e in result["edges"]), expected, abs_tol=1e-6)
            for u, v, edge in zip(result["nodes"], result["nodes"][1:], result["edges"]):
                assert edge.to == v and edge in graph.adjacency[u], "Nonexistent road edge"
            for u, edges in graph.adjacency.items():
                for edge in edges:
                    assert metres(graph.nodes[u], graph.nodes[goal]) <= edge.length+metres(graph.nodes[edge.to], graph.nodes[goal])+1e-6
            reachable += 1
    nodes = [dict(type="node", id=i, lat=30.05, lon=31.34+i*.001) for i in (1, 2, 3)]
    one_way = {"elements": nodes+[dict(type="way", id=10, nodes=[1, 2, 3],
        tags={"highway": "residential", "oneway": "yes"})]}
    graph = build_graph(one_way)
    assert astar(graph, 1, 3)["nodes"] == [1, 2, 3]
    try:
        astar(graph, 3, 1)
    except RoutingError:
        pass
    else:
        raise AssertionError("Wrong-way route")
    assert astar(graph, 2, 2)["distance_m"] == 0
    print(f"PASS: {reachable+unreachable} A*/Dijkstra cases ({reachable} reachable, {unreachable} unreachable).")
    print("PASS: optimal costs, valid directed edges, heuristic consistency, one-way and start=goal.")
    print("Synthetic tests verify the algorithm/model; they do not certify live OSM completeness or real driving legality.")


if __name__ == "__main__":
    run()
