import app.services.prediction.graph_from_text as gft


class FakeProject:
    def __init__(self):
        self.project_id = "proj_x"
        self.chunk_size = 500
        self.chunk_overlap = 50
        self.ontology = None
        self.simulation_requirement = None
        self.graph_id = None


class FakeProjectManager:
    created = None
    saved_text = None
    saved = None

    @classmethod
    def create_project(cls, name="Unnamed Project"):
        cls.created = FakeProject()
        return cls.created

    @classmethod
    def save_extracted_text(cls, project_id, text):
        cls.saved_text = (project_id, text)

    @classmethod
    def save_project(cls, project):
        cls.saved = project


class FakeOntology:
    def generate(self, texts, simulation_requirement, additional_context=None):
        return {"entity_types": ["Person"], "edge_types": [], "analysis_summary": "sum"}


class FakeGraphBuilder:
    def __init__(self, *a, **k):
        self.calls = []

    def create_graph(self, name):
        self.calls.append(("create_graph", name))
        return "mirofish_graph1"

    def set_ontology(self, graph_id, ontology):
        self.calls.append(("set_ontology", graph_id))

    def add_text_batches(self, graph_id, chunks, batch_size=3, progress_callback=None):
        self.calls.append(("add_text_batches", len(chunks)))
        return ["ep1", "ep2"]

    def _wait_for_episodes(self, episode_uuids, progress_callback=None, timeout=600):
        self.calls.append(("_wait_for_episodes", len(episode_uuids)))


class FakeTextProcessor:
    @staticmethod
    def split_text(text, chunk_size, overlap):
        return ["chunk1", "chunk2"]


def _wire(monkeypatch):
    fb = FakeGraphBuilder()
    monkeypatch.setattr(gft, "ProjectManager", FakeProjectManager)
    monkeypatch.setattr(gft, "OntologyGenerator", lambda *a, **k: FakeOntology())
    monkeypatch.setattr(gft, "GraphBuilderService", lambda *a, **k: fb)
    monkeypatch.setattr(gft, "TextProcessor", FakeTextProcessor)
    return fb


def test_build_graph_from_text_returns_graph_id_and_runs_sequence(monkeypatch):
    fb = _wire(monkeypatch)
    graph_id = gft.build_graph_from_text("brief body", "Resolves YES if X.", name="brief-v2")
    assert graph_id == "mirofish_graph1"
    # project got the raw text + ontology + graph_id, and was saved
    assert FakeProjectManager.saved_text == ("proj_x", "brief body")
    assert FakeProjectManager.saved.graph_id == "mirofish_graph1"
    assert FakeProjectManager.saved.simulation_requirement == "Resolves YES if X."
    # the build sequence ran in order
    kinds = [c[0] for c in fb.calls]
    assert kinds == ["create_graph", "set_ontology", "add_text_batches", "_wait_for_episodes"]
