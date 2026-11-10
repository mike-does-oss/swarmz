"""
Build a knowledge graph (swarmz: local Graphiti memory, formerly Zep) directly from brief text (spec: the frozen brief is
the MiroFish seed). Replicates the HTTP /api/graph build sequence programmatically
so a prediction run needs no file upload. Blocks until Zep finishes ingesting.
"""

from ..text_processor import TextProcessor
from ..ontology_generator import OntologyGenerator
from ..graph_builder import GraphBuilderService
from ...models.project import ProjectManager
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.graph_from_text')


def build_graph_from_text(brief_text: str, requirement: str, name: str = "brief-v2",
                          graph_id: str | None = None) -> str:
    """swarmz: graph_id lets the caller pick the graph name (q_<qid>__<arm>__<run>)."""
    project = ProjectManager.create_project(name)
    ProjectManager.save_extracted_text(project.project_id, brief_text)

    ontology = OntologyGenerator().generate([brief_text], simulation_requirement=requirement)
    project.ontology = ontology
    project.simulation_requirement = requirement

    builder = GraphBuilderService()
    chunks = TextProcessor.split_text(brief_text, project.chunk_size, project.chunk_overlap)
    graph_id = builder.create_graph(name) if graph_id is None else builder.create_graph(name, graph_id=graph_id)
    project.graph_id = graph_id
    ProjectManager.save_project(project)

    builder.set_ontology(graph_id, ontology)
    episode_uuids = builder.add_text_batches(graph_id, chunks, batch_size=3)
    builder._wait_for_episodes(episode_uuids)

    logger.info(f"built graph {graph_id} from brief ({len(chunks)} chunks)")
    return graph_id
