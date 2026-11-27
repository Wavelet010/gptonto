"""Compile an explicitly supported, closed-world extraction subset of OWL."""
from rdflib import Graph, URIRef, RDF, RDFS, OWL, XSD


def compile_ttl(text):
    graph = Graph()
    graph.parse(data=text, format="turtle", publicID="urn:gptonto:ontology:")
    if list(graph.objects(None, OWL.imports)):
        raise ValueError("owl:imports must be resolved into a local combined TTL before upload")
    classes = {s for kind in (OWL.Class, RDFS.Class)
               for s in graph.subjects(RDF.type, kind) if isinstance(s, URIRef)}
    if not classes:
        raise ValueError("TTL contains no named classes")
    def label(node):
        return str(graph.value(node, RDFS.label) or str(node).rsplit("#", 1)[-1].rsplit("/", 1)[-1])
    def supers(node):
        result, pending = {node}, [node]
        while pending:
            for parent in graph.objects(pending.pop(), RDFS.subClassOf):
                if parent in classes and parent not in result:
                    result.add(parent)
                    pending.append(parent)
        return sorted(map(str, result))
    schema = {"classes": {}, "properties": {}, "relationships": {}, "warnings": []}
    for node in sorted(classes, key=str):
        schema["classes"][str(node)] = {"label": label(node),
            "description": str(graph.value(node, RDFS.comment) or ""),
            "ancestors": supers(node)}
    for kind, key in ((OWL.DatatypeProperty, "properties"), (OWL.ObjectProperty, "relationships")):
        for node in sorted(set(graph.subjects(RDF.type, kind)), key=str):
            if not isinstance(node, URIRef):
                continue
            domains = list(graph.objects(node, RDFS.domain))
            ranges = list(graph.objects(node, RDFS.range))
            if not domains or not ranges or any(d not in classes for d in domains):
                schema["warnings"].append(f"Excluded property without named local domain/range: {node}")
                continue
            if kind == OWL.ObjectProperty and any(r not in classes for r in ranges):
                schema["warnings"].append(f"Excluded relationship with unknown range: {node}")
                continue
            if kind == OWL.DatatypeProperty and (len(ranges) != 1 or str(ranges[0]) not in DATATYPES):
                schema["warnings"].append(f"Excluded unsupported datatype: {node}")
                continue
            schema[key][str(node)] = {"label": label(node),
                "description": str(graph.value(node, RDFS.comment) or ""),
                "domain": sorted(map(str, domains)), "range": sorted(map(str, ranges))}
    if any(graph.subjects(RDF.type, OWL.Restriction)):
        schema["warnings"].append("OWL restrictions/cardinality are not enforced by this MVP; review before use")
    return schema


DATATYPES = {str(XSD)+x for x in (
    "string", "boolean", "integer", "int", "long", "nonNegativeInteger",
    "positiveInteger", "decimal", "double", "float", "date", "dateTime", "anyURI"
)}


def applies(schema, class_uri, domains):
    return set(domains).issubset(schema["classes"].get(class_uri, {}).get("ancestors", []))


def typed_value(value, datatype):
    import math
    from datetime import date, datetime
    from urllib.parse import urlsplit
    kind = datatype.rsplit("#", 1)[-1]
    if kind == "boolean":
        return type(value) is bool
    if kind in {"integer", "int", "long", "nonNegativeInteger", "positiveInteger"}:
        return (type(value) is int
            and (kind != "nonNegativeInteger" or value >= 0)
            and (kind != "positiveInteger" or value > 0)
            and (kind != "int" or -(2**31) <= value < 2**31)
            and (kind != "long" or -(2**63) <= value < 2**63))
    if kind in {"decimal", "double", "float"}:
        return type(value) in (int, float) and math.isfinite(value)
    if not isinstance(value, str):
        return False
    try:
        if kind == "date":
            date.fromisoformat(value)
        elif kind == "dateTime":
            if "T" not in value:
                return False
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif kind == "anyURI":
            return bool(urlsplit(value).scheme)
        return True
    except ValueError:
        return False


def validate(raw, schema, text):
    """Validate individual assertions; rejected assertions remain visible."""
    entities, relationships, errors = [], [], []
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), list) for k in ("entities", "relationships")):
        raise ValueError("Expected entities and relationships arrays")
    def evidence(record):
        value = record.get("evidence")
        return isinstance(value, str) and bool(value.strip()) and value in text
    from collections import Counter
    counts = Counter(e["local_id"] for e in raw["entities"]
                     if isinstance(e, dict) and isinstance(e.get("local_id"), str))
    by_id = {}
    for e in raw["entities"]:
        if not isinstance(e, dict):
            errors.append("Invalid entity record"); continue
        eid, cls, name = e.get("local_id"), e.get("class_uri"), e.get("name")
        if not all(isinstance(x, str) and x.strip() for x in (eid, cls, name)) or counts[eid] != 1 or cls not in schema["classes"] or not evidence(e):
            errors.append(f"Invalid entity: {eid}"); continue
        attrs = []
        values = e.get("attributes", [])
        if not isinstance(values, list):
            errors.append(f"Invalid attributes list: {eid}"); values = []
        for a in values:
            if not isinstance(a, dict) or not isinstance(a.get("property_uri"), str):
                errors.append(f"Invalid attribute: {eid}"); continue
            uri = a["property_uri"]
            prop = schema["properties"].get(uri)
            if not prop or not applies(schema, cls, prop["domain"]) or not typed_value(a.get("value"), prop["range"][0]) or not evidence(a):
                errors.append(f"Invalid attribute: {eid} {uri}"); continue
            attrs.append({"property_uri": uri, "value": a["value"], "datatype": prop["range"][0], "evidence": a["evidence"]})
        clean = {"local_id": eid, "class_uri": cls, "name": name, "evidence": e["evidence"], "attributes": attrs}
        by_id[eid] = clean
        entities.append(clean)
    for r in raw["relationships"]:
        if not isinstance(r, dict) or not all(isinstance(r.get(k), str) for k in ("source", "target", "predicate_uri")):
            errors.append("Invalid relationship record"); continue
        source, target = by_id.get(r["source"]), by_id.get(r["target"])
        pred = schema["relationships"].get(r["predicate_uri"])
        if not source or not target or not pred or not applies(schema, source["class_uri"], pred["domain"]) or not applies(schema, target["class_uri"], pred["range"]) or not evidence(r):
            errors.append(f"Invalid relationship: {r['predicate_uri']}"); continue
        relationships.append({k: r[k] for k in ("source", "target", "predicate_uri", "evidence")})
    return {"entities": entities, "relationships": relationships, "errors": errors}
