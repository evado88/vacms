Implement a fully dynamic, self-evolving Rumor Word Cloud + Network Intelligence Visualization in VACMS.

This feature must combine:
1) Phrase-based word cloud (frequency-driven)
2) Semantic clustering (NLP-driven)
3) Network graph structure (relationship-driven)

The result should behave like a living, intelligent rumor analytics engine — not a static chart.

---------------------------------------------------------------------
DATA SOURCE
---------------------------------------------------------------------
Use ONLY live data from:

public.va_data_management_csadailytracker.desci

DO NOT:
- use CSV files
- hardcode any rumor list
- rely on static datasets

The system must continuously adapt to new incoming rumor text.

---------------------------------------------------------------------
CORE BEHAVIOR
---------------------------------------------------------------------
The system must:

- Treat current rumors as a seed set ONLY
- Automatically detect new phrases as they appear
- Merge similar phrases into clusters dynamically
- Create new clusters when distinct phrases emerge
- Maintain relationships between phrases (linked rumors)

IMPORTANT:
Do NOT split into meaningless single words.
Use short phrases that preserve meaning and context.

---------------------------------------------------------------------
BACKEND (NLP + PROCESSING PIPELINE)
---------------------------------------------------------------------
Build a reusable rumor processing pipeline:

1. Data cleaning:
   - remove null / blank / whitespace entries

2. Normalization:
   - lowercase
   - trim spacing
   - remove noise punctuation

3. Tokenization (phrase-aware)

4. Spelling correction:
   - lightweight rules + optional fuzzy matching

5. Phrase standardization:
   - detect incomplete phrases
   - expand intelligently using context

6. Dynamic clustering:
   - NO predefined categories
   - cluster based on similarity

7. Similarity methods:
   - trigram similarity
   - Levenshtein distance
   - token overlap scoring

8. Relationship detection:
   - shared tokens
   - semantic similarity
   - co-occurrence

9. Maintain:
   - compound clusters (e.g. “birth certificate and death certificate”)
   - atomic clusters (e.g. “birth certificate”)

10. Optional dictionary:
   - synonym/correction mapping
   - must NOT be required for system to function

---------------------------------------------------------------------
SPECIAL LOGIC (EXAMPLES – NOT HARD CODED)
---------------------------------------------------------------------
Examples must be handled dynamically:

- “Birth certificate and death”
  → “Birth certificate and death certificate”

- “Birth certificate”
  → remains independent
  → linked to compound phrase

- “Martenal health” → “Maternal health”
- “Health talk” ↔ “Health education”

These must be implemented via pattern recognition and fuzzy logic.

---------------------------------------------------------------------
FRONTEND (REACT + D3)
---------------------------------------------------------------------
Use:
- d3-cloud for word layout
- d3-force for network simulation

(Note: d3-cloud handles word placement and collision detection :contentReference[oaicite:0]{index=0})

---------------------------------------------------------------------
VISUAL DESIGN (CRITICAL – NETWORK STRUCTURE)
---------------------------------------------------------------------

The visualization MUST NOT be a traditional flat word cloud.

Instead, implement a HYBRID structure:

1) WORD CLOUD LAYER
- phrases sized by frequency
- horizontal orientation only
- bold sans-serif font
- responsive layout

2) NETWORK MESH LAYER (PRIMARY DIFFERENTIATOR)

- each phrase = node
- relationships = edges

Structure characteristics:
- nodes connected via thin lines
- forms triangles, clusters, irregular polygons
- resembles:
  - constellation map
  - digital network mesh
- dense clusters = highly related rumors
- sparse links = weaker relationships

3) FORCE-DRIVEN LAYOUT

Use d3-force:
- attraction → similar phrases
- repulsion → avoid overlap
- collision handling for text bounding boxes

Force-directed layouts are commonly used to position nodes dynamically based on relationships :contentReference[oaicite:1]{index=1}

4) DYNAMIC BEHAVIOR

- nodes reposition smoothly when data changes
- layout evolves as new rumors enter system
- no hard refresh required

5) INTERACTION

- hover:
  - highlight connected nodes
  - emphasize cluster
- tooltip shows:
  - canonical phrase
  - frequency
  - percentage
  - related phrases
  - raw examples

---------------------------------------------------------------------
COLOR & STYLING
---------------------------------------------------------------------

DO NOT hardcode colors.

- Colors must be configurable at implementation
- Support theming (light/dark/future branding)

---------------------------------------------------------------------
WOW-FACTOR FEATURES
---------------------------------------------------------------------

1) Multi-view:
   - Hybrid Cloud + Network (default)
   - Full Network Graph
   - Ranked Table

2) Self-learning clustering:
   - improves with new data
   - configurable thresholds

3) Trend analysis:
   - rising rumors
   - declining rumors
   - emerging rumors

4) Data quality intelligence:
   - spelling corrections tracked
   - cluster merges tracked
   - new clusters flagged

5) Filtering:
   - date range
   - province
   - district
   - source
   - category
   - frequency threshold

6) Export:
   - PNG (visual)
   - CSV (clusters)
   - CSV (relationships)

---------------------------------------------------------------------
ACCEPTANCE CRITERIA
---------------------------------------------------------------------

- Uses ONLY live VACMS data
- No static datasets or hardcoded vocabularies
- Automatically adapts to new rumors
- Clusters evolve over time
- Related rumors are visibly linked
- Visualization behaves like a living system
- Combines:
  - word cloud (importance)
  - network graph (relationships)
- Feels like an intelligent analytics engine, not a chart

---------------------------------------------------------------------

END GOAL:

A dynamic, intelligent, network-driven rumor visualization system that:
- continuously learns from incoming data
- reveals hidden relationships
- visually represents rumor ecosystems as a living, evolving structure
