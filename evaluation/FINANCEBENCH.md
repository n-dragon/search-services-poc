# FinanceBench diagnostic on the demo VM

Latest execution: [25 October 2026 — results and failure analysis](reports/financebench-2026-10-25.md).

This is an executed-data diagnostic for the public FinanceBench corpus, not a
reproduction of Mistral's agentic QA scores. It produces no model answers and no
QA accuracy. The demo's Vespa index is not modified.

Source: [patronus-ai/financebench](https://github.com/patronus-ai/financebench),
pinned at `cc39aeb4afdf33909ee1412188bf89035950c2eb`. The checked-in manifest
records sizes and Git blob SHA-1 digests for all 368 original PDFs and the two
dataset files. All downloads are verified. Questions, reference answers,
justifications, and annotated evidence are excluded from the retrieval index.

## Run

On the VM, with Python 3, SQLite FTS5, Poppler `pdftotext`, and at least 2 GB free:

```bash
python3 -m scripts.financebench_diagnostic --output-dir benchmark-results
```

The manual **Diagnose FinanceBench on demo VM** workflow runs tests first,
uses pinned SSH host keys, downloads approximately 705 MB to the VM, extracts
PDF pages sequentially, and executes the diagnostic there at low CPU/IO priority.
No external model API is called. The first run may take 10–25 minutes.
The corpus and isolated index persist in `benchmark-data/financebench/`.
Reports persist under `benchmark-results/` and as 30-day Actions artifacts.

## Predeclared diagnostic variants

1. Global SQLite FTS5 BM25, English Porter stemming, a whole PDF page per result,
   fixed question-only lexical terms, top five pages.
2. The same retrieval, scoped to a company inferred from question text and
   **all-corpus document metadata**. No gold company, document, page, answer,
   justification, or evidence is used to choose a query or filter.
3. The global top five pages plus their immediate neighboring pages in the
   same source, with deduplication. This reads up to 15 pages, so its coverage
   cannot be interpreted as a gain at equal read/token budget.

All 150 questions remain in the denominator, including unavailable evidence
pages. Metrics are any/all annotated-page recovery, mean annotated-page recall,
and annotated-document hits. Annotated evidence may be incomplete: missing its
page is not a proof of an incorrect answer. A token-overlap check below 0.8
flags an extraction case for inspection; it does not prove a parser failure.
No answer, citation, reasoning, or model-cost score is invented.

The report records corpus, script and manifest hashes, parser and SQLite
versions, per-question IDs, page coordinates and retrieval candidates. It does
not publish the answer key into a searchable corpus. All questions are used
for diagnosis; proposed improvements require a separate validation set.

## Full article replication prerequisites

The [Mistral article](https://mistral.ai/news/agentic-search/) compares one-shot
RAG, an iterative search loop, and a loop with navigation, using MM 3.5 and
GLM-5.2 over FinanceBench and OfficeQA Pro. To run comparable QA experiments,
we still need model access, a bounded LLM agent and calibrated judge, Search
Toolkit's parsing/embedding/index configuration, and pinned corpus manifests.
The article does not supply all prompts, budgets or an executable evaluation
harness. OfficeQA Pro currently requires an approved Hugging Face dataset
access grant as well as a token. Do not retrieve gated answer keys through
older mirrors as a workaround.

Named credential checks return only booleans for the VM project's existing
configuration and the workflow's `demo` environment. They do not enumerate or
export account credentials. No model is run merely because a key exists.

## Interpreting improvement candidates

- Low evidence-text overlap: inspect the original page and table structure;
  compare local extraction with OCR before blaming retrieval.
- Correct source, missing page: evaluate document-local `grep`, source offsets,
  page navigation, chunking and financial query rewriting.
- Recovery after question-derived company scoping: evaluate entity-aware
  filters, keeping a global fallback for ambiguous company names.
- Recovery after neighbor expansion: test adaptive context expansion while
  reporting the extra pages, tokens, calls, and latency.
- Multiple annotated pages / numerical-reasoning labels: only an actual QA
  agent and verified calculations can establish whether the answer fails.

Citation: Islam et al., *FinanceBench: A New Benchmark for Financial Question
Answering*, 2023, [arXiv:2311.11944](https://arxiv.org/abs/2311.11944).
