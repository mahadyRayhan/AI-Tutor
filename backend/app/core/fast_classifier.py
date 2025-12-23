import logging
from gliner import GLiNER
from sentence_transformers import SentenceTransformer, util
from app.core import config

logger = logging.getLogger(__name__)

class FastClassifier:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FastClassifier, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        logger.info(f"🚀 Loading Fast Models into: {config.MODELS_CACHE_DIR}")
        
        # 1. Intent Model (Semantic Similarity)
        # Using cache_folder to store in your project directory
        self.intent_model = SentenceTransformer('all-MiniLM-L6-v2', cache_folder=str(config.MODELS_CACHE_DIR))
        
        self.intent_anchors = {
            "REVIEW": "Here is my code: int main() { return 0; }. Is this correct? Review this snippet.",
            "CONCEPT": "What is a variable? Explain the concept of recursion. Define array.",
            "PROBLEM": "How do I write a loop? Solve this problem. Write code to sum numbers.",
            "DEBUG": "Why is this error happening? Fix my segmentation fault. It's not compiling."
        }
        self.anchor_embeddings = {k: self.intent_model.encode(v) for k, v in self.intent_anchors.items()}

        # 2. Entity Extraction Model (GLiNER)
        # We use a try-catch to handle the specific loading pattern of GLiNER
        try:
            self.ner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1") # Gliner manages its own cache usually
        except Exception as e:
            logger.warning(f"GLiNER load warning: {e}. Trying default cache.")
            self.ner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")
        
        self.ner_labels = ["programming concept", "data type", "function", "control flow", "error"]
        
        logger.info("✅ Fast Models Loaded.")

    def classify_intent(self, query: str) -> str:
        # Heuristic: Code snippets are usually reviews
        if "{" in query and "}" in query and ";" in query:
            return "REVIEW"
            
        query_emb = self.intent_model.encode(query)
        scores = {k: util.cos_sim(query_emb, v).item() for k, v in self.anchor_embeddings.items()}
        return max(scores, key=scores.get)

    def extract_entities(self, query: str) -> list:
        # returns list of strings
        entities = self.ner_model.predict_entities(query, self.ner_labels)
        unique = list(set([e['text'] for e in entities]))
        return unique

fast_classifier = FastClassifier()