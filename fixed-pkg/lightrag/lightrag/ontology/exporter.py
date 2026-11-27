"""RDF instance export (ABox only); source provenance stays in JSON."""
from rdflib import Graph, URIRef, Literal, RDF, RDFS


def rdf_turtle(data):
    graph = Graph()
    def instance(id):
        return URIRef("urn:gptonto:instance:"+id)
    for entity in data["entities"]:
        node = instance(entity["id"])
        graph.add((node,RDF.type,URIRef(entity["class_uri"])))
        graph.add((node,RDFS.label,Literal(entity["name"])))
        for attr in entity["attributes"]:
            graph.add((node,URIRef(attr["property_uri"]),
                       Literal(attr["value"],datatype=URIRef(attr["datatype"]))))
    for relation in data["relationships"]:
        graph.add((instance(relation["source"]),URIRef(relation["predicate_uri"]),instance(relation["target"])))
    return graph.serialize(format="turtle")
