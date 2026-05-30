import osmnx as ox
import matplotlib.pyplot as plt

ox.settings.log_console = True
ox.settings.use_cache = True

address = "Malteserstraße 139, Berlin, Germany"
point = ox.geocoder.geocode(address)
print("Point:", point)

G = ox.graph.graph_from_point(point, dist=200, network_type="all")
print("Nodes:", len(G.nodes))
print("Edges:", len(G.edges))

# convert graph to GeoDataFrames
nodes, edges = ox.convert.graph_to_gdfs(G)

# save a plot
fig, ax = plt.subplots(figsize=(8, 8))
edges.plot(ax=ax, linewidth=1)
ax.scatter(point[1], point[0], s=40)   # x=lon, y=lat
plt.savefig("malteser_all.png", dpi=200, bbox_inches="tight")
print("Saved plot as malteser_all.png")

# inspect useful columns
print("\nAvailable edge columns:")
print(edges.columns.tolist())

wanted = ["name", "highway", "service", "cycleway", "lanes", "maxspeed", "oneway"]
existing = [c for c in wanted if c in edges.columns]

print("\nRelevant edge tags:")
safe_edges = edges[existing].copy()

for col in safe_edges.columns:
    safe_edges[col] = safe_edges[col].apply(
        lambda x: ", ".join(map(str, x)) if isinstance(x, list) else x
    )

print(safe_edges.drop_duplicates().to_string())