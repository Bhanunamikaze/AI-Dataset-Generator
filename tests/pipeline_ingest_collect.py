from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import ROOT_DIR, run_script

class CollectorTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # web.py — unit tests (no network required)
    # ------------------------------------------------------------------

    def test_chunk_text_returns_single_chunk_for_short_input(self) -> None:
        from scripts.utils.web import chunk_text

        text = "This is a short document."
        chunks = chunk_text(text, max_chars=500, overlap=50)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_chunk_text_splits_long_input(self) -> None:
        from scripts.utils.web import chunk_text

        # 10 paragraphs each ~120 chars
        para = "A" * 120
        text = "\n\n".join([para] * 10)
        chunks = chunk_text(text, max_chars=300, overlap=0)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 400)  # some tolerance for overlap

    def test_chunk_text_returns_empty_for_blank_input(self) -> None:
        from scripts.utils.web import chunk_text

        self.assertEqual(chunk_text("   ", max_chars=500), [])
        self.assertEqual(chunk_text("", max_chars=500), [])

    def test_read_local_file_returns_content(self) -> None:
        from scripts.utils.web import read_local_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello, collector!")
            file_path = f.name

        try:
            content = read_local_file(file_path)
            self.assertEqual(content, "Hello, collector!")
        finally:
            Path(file_path).unlink(missing_ok=True)

    def test_walk_repo_filters_by_extension(self) -> None:
        from scripts.utils.web import walk_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "file.md").write_text("# Title\nContent.", encoding="utf-8")
            (p / "file.py").write_text("print('hello')", encoding="utf-8")
            (p / "file.bin").write_bytes(b"\x00\x01\x02")

            md_only = walk_repo(p, extensions={".md"}, max_files=50)
            py_and_md = walk_repo(p, extensions={".md", ".py"}, max_files=50)

        self.assertEqual(len(md_only), 1)
        self.assertTrue(md_only[0].path.endswith(".md"))

        extensions_found = {lf.extension for lf in py_and_md}
        self.assertIn(".md", extensions_found)
        self.assertIn(".py", extensions_found)
        self.assertNotIn(".bin", extensions_found)

    def test_extract_text_strips_html_tags_with_stdlib_fallback(self) -> None:
        from scripts.utils.web import extract_text

        sample_html = (
            "<html><head><title>Test Page</title></head>"
            "<body><p>Hello world</p><script>ignored()</script></body></html>"
        )
        result = extract_text(sample_html, url="http://example.com/")
        self.assertIn("Hello world", result.text)
        self.assertIn("Test Page", result.title)
        self.assertNotIn("ignored()", result.text)

    # ------------------------------------------------------------------
    # collect.py — integration tests (no network required)
    # ------------------------------------------------------------------

    def test_collect_from_local_files_produces_valid_canonical_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            output_path = temp_dir / "collected.jsonl"

            # Write sample local files
            (temp_dir / "guide.md").write_text(
                "# Linux Permissions\n\nThe `chmod` command changes file permissions.\n\n"
                "Use `chmod 755` to set rwxr-xr-x on a file.",
                encoding="utf-8",
            )
            (temp_dir / "notes.txt").write_text(
                "File ownership is changed with chown. "
                "For example, `chown user:group file` sets the owner and group.",
                encoding="utf-8",
            )

            result = run_script(
                "scripts/collect.py",
                "--paths", str(temp_dir / "guide.md"), str(temp_dir / "notes.txt"),
                "--output", str(output_path),
                "--tool-context", "codex",
            )
            summary = json.loads(result.stdout)

            self.assertGreater(summary["records_collected"], 0)
            self.assertEqual(summary["output"], str(output_path))
            self.assertTrue(output_path.exists())

            # Validate each record is a parseable dict with required keys
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreater(len(records), 0)
            for rec in records:
                self.assertIn("id", rec)
                self.assertIn("instruction", rec)
                self.assertIn("response", rec)
                self.assertIn("metadata", rec)
                self.assertEqual(rec["status"], "collected")
                self.assertIn(rec["source_type"], ("url_reference", "internet_research"))

    def test_collect_from_local_files_output_imports_into_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            collected_path = temp_dir / "collected.jsonl"
            db_path = temp_dir / "state.sqlite"

            (temp_dir / "doc.md").write_text(
                "# Bash Scripting\n\nUse `set -euo pipefail` at the top of every script.\n\n"
                "Quote all variable expansions to prevent word splitting.",
                encoding="utf-8",
            )

            # Step 1: collect
            run_script(
                "scripts/collect.py",
                "--paths", str(temp_dir / "doc.md"),
                "--output", str(collected_path),
                "--tool-context", "codex",
            )

            # Step 2: import collected JSONL into the pipeline db
            gen_result = run_script(
                "scripts/generate.py",
                "--input", str(collected_path),
                "--source-type", "url_reference",
                "--db", str(db_path),
                "--tool-context", "codex",
            )
            gen_summary = json.loads(gen_result.stdout)

            self.assertGreater(gen_summary["imported"], 0)

            # Verify records exist in the DB
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(db_path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            finally:
                conn.close()
            self.assertGreater(count, 0)

    def test_collect_fails_gracefully_with_no_source(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/collect.py"],
            cwd=str(ROOT_DIR),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_collect_from_directory_walk_respects_extension_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            output_path = temp_dir / "collected.jsonl"

            (temp_dir / "a.md").write_text("# A\nMarkdown content here.", encoding="utf-8")
            (temp_dir / "b.py").write_text("def foo():\n    pass\n", encoding="utf-8")
            (temp_dir / "c.csv").write_text("col1,col2\nval1,val2\n", encoding="utf-8")

            run_script(
                "scripts/collect.py",
                "--paths", str(temp_dir),
                "--extensions", "md",
                "--output", str(output_path),
                "--tool-context", "codex",
            )

            records = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            source_paths = [r.get("source_uri", r.get("metadata", {}).get("source_path", "")) for r in records]
            for path in source_paths:
                self.assertFalse(path.endswith(".py"), f"Should not collect .py files: {path}")
                self.assertFalse(path.endswith(".csv"), f"Should not collect .csv files: {path}")

    def test_source_artifact_validation_accepts_file_artifact(self) -> None:
        from scripts.utils.schema import validate_source_artifact

        artifact = {
            "id": "art_123",
            "artifact_type": "file",
            "kind": "c_source",
            "source_path": "/tmp/example.c",
            "title": "example.c",
            "language": "c_cpp",
            "content": "int main(void) { return 0; }",
            "related_paths": [],
            "metadata": {"sha256": "abc"},
        }

        self.assertEqual(validate_source_artifact(artifact), [])

    def test_discovery_classifies_structured_source_types(self) -> None:
        from scripts.utils.discovery import classify_source_path

        self.assertEqual(classify_source_path("src/main.cpp")["parser_key"], "c_family")
        self.assertEqual(classify_source_path("include/main.h")["file_kind"], "c_header")
        self.assertEqual(classify_source_path("decoder.asm")["file_kind"], "assembly_source")
        self.assertEqual(classify_source_path("shared.inc")["file_kind"], "assembly_include")
        self.assertEqual(classify_source_path("sample.sln")["file_kind"], "visual_studio_solution")
        self.assertEqual(classify_source_path("project.vcxproj")["parser_key"], "c_family")
        self.assertEqual(classify_source_path("guide.html")["parser_key"], "article")
        self.assertEqual(classify_source_path("archive.mhtml")["file_kind"], "mhtml_document")
        self.assertFalse(classify_source_path("image.png")["supported"])

    def test_discovery_reads_only_supported_source_files(self) -> None:
        from scripts.utils.discovery import discover_source_files

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            (temp_dir / "src").mkdir()
            (temp_dir / ".git").mkdir()
            (temp_dir / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            (temp_dir / "src" / "main.h").write_text("int main();\n", encoding="utf-8")
            (temp_dir / "src" / "decoder.asm").write_text("Decoder PROC\n nop\nDecoder ENDP\n", encoding="utf-8")
            (temp_dir / "src" / "shared.inc").write_text("PUBLIC SharedLabel\n", encoding="utf-8")
            (temp_dir / "guide.html").write_text("<html><body><pre>printf(\"hi\");</pre></body></html>", encoding="utf-8")
            (temp_dir / ".git" / "ignored.cpp").write_text("int ignored() { return 1; }\n", encoding="utf-8")
            (temp_dir / "image.png").write_bytes(b"\x89PNG\r\n")

            discovered = discover_source_files([str(temp_dir)], max_files=20)

        self.assertEqual(discovered["skipped"], [])
        file_paths = {Path(item["source_path"]).name for item in discovered["files"]}
        self.assertEqual(file_paths, {"main.cpp", "main.h", "decoder.asm", "shared.inc", "guide.html"})

    def test_c_family_parser_bundles_related_source_and_header_files(self) -> None:
        from scripts.utils.discovery import discover_source_files
        from scripts.utils.parsers.c_family import parse_c_family_corpus

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            (temp_dir / "src").mkdir()
            (temp_dir / "include").mkdir()
            (temp_dir / "app.sln").write_text(
                'Project("{GUID}") = "demo", "demo.vcxproj", "{PROJECT-GUID}"\n',
                encoding="utf-8",
            )
            (temp_dir / "demo.vcxproj").write_text(
                "<Project><ItemGroup>"
                '<ClCompile Include="src/main.cpp" />'
                '<ClInclude Include="include/main.h" />'
                '<MASM Include="src/decoder.asm" />'
                '<MASM Include="src/shared.inc" />'
                "</ItemGroup></Project>",
                encoding="utf-8",
            )
            (temp_dir / "src" / "main.cpp").write_text(
                '#include "main.h"\nint add(int a, int b) { return a + b; }\n',
                encoding="utf-8",
            )
            (temp_dir / "include" / "main.h").write_text(
                "int add(int a, int b);\n",
                encoding="utf-8",
            )
            (temp_dir / "src" / "decoder.asm").write_text(
                "INCLUDE shared.inc\nDecoder PROC\n nop\nDecoder ENDP\nSharedLabel:\n ret\n",
                encoding="utf-8",
            )
            (temp_dir / "src" / "shared.inc").write_text(
                "PUBLIC SharedLabel\n",
                encoding="utf-8",
            )

            discovered = discover_source_files([str(temp_dir)], max_files=20)
            c_family_files = [
                item for item in discovered["files"]
                if item["metadata"]["parser_key"] == "c_family"
            ]
            parsed = parse_c_family_corpus(c_family_files, bundle_max_chars=6000)

        self.assertGreaterEqual(len(parsed["bundles"]), 1)
        bundle = parsed["bundles"][0]
        self.assertEqual(bundle["kind"], "c_family_context")
        self.assertIn("main.cpp", bundle["content"])
        self.assertIn("decoder.asm", bundle["content"])
        self.assertIn("shared.inc", bundle["content"])
        self.assertIn("include/main.h", "\n".join(bundle["metadata"]["include_lines"]))
        self.assertIn("shared.inc", "\n".join(bundle["metadata"]["include_lines"]))
        self.assertIn("demo", bundle["metadata"]["project_names"])
        self.assertIn("decoder.asm", "\n".join(bundle["metadata"]["assembly_files"]))
        self.assertIn("shared.inc", "\n".join(bundle["metadata"]["assembly_files"]))
        self.assertIn("Decoder", " ".join(bundle["metadata"]["symbol_names"]))
        relation_kinds = {item["kind"] for item in parsed["relations"]}
        self.assertIn("includes", relation_kinds)
        self.assertIn("project_contains_file", relation_kinds)

    def test_c_family_parser_falls_back_to_heuristics_without_tree_sitter(self) -> None:
        from scripts.utils.discovery import discover_source_files
        from scripts.utils.parsers import c_family

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            (temp_dir / "src").mkdir()
            (temp_dir / "include").mkdir()
            (temp_dir / "src" / "main.cpp").write_text(
                '#include "main.hpp"\nnamespace demo { int run() { return 1; } }\n',
                encoding="utf-8",
            )
            (temp_dir / "include" / "main.hpp").write_text(
                "#define MAX_VALUE 5\nnamespace demo { class Widget {}; }\n",
                encoding="utf-8",
            )
            (temp_dir / "src" / "decoder.asm").write_text(
                "Decoder PROC\n nop\nDecoder ENDP\nSharedLabel:\n ret\n",
                encoding="utf-8",
            )

            discovered = discover_source_files([str(temp_dir)], max_files=20)
            c_family_files = [
                item for item in discovered["files"]
                if item["metadata"]["parser_key"] == "c_family"
            ]
            c_family._get_tree_sitter_parser.cache_clear()
            with patch("scripts.utils.parsers.c_family._load_tree_sitter_language_pack", return_value=None):
                parsed = c_family.parse_c_family_corpus(c_family_files, bundle_max_chars=6000)

        self.assertGreaterEqual(len(parsed["bundles"]), 1)
        self.assertTrue(parsed["units"])
        self.assertTrue(all(item["metadata"]["parser_mode"] == "heuristic" for item in parsed["units"]))
        self.assertEqual(parsed["bundles"][0]["metadata"]["parser_mode"], "heuristic")

    def test_c_family_parser_uses_tree_sitter_when_available(self) -> None:
        if importlib.util.find_spec("tree_sitter_language_pack") is None:
            self.skipTest("tree_sitter_language_pack is not installed")

        from scripts.utils.discovery import discover_source_files
        from scripts.utils.parsers import c_family

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            (temp_dir / "src").mkdir()
            (temp_dir / "include").mkdir()
            (temp_dir / "src" / "main.cpp").write_text(
                '#include "main.hpp"\nnamespace demo { int run() { return 1; } }\n',
                encoding="utf-8",
            )
            (temp_dir / "include" / "main.hpp").write_text(
                "#define MAX_VALUE 5\nnamespace demo { class Widget {}; typedef unsigned int DWORD; }\n",
                encoding="utf-8",
            )
            (temp_dir / "src" / "decoder.asm").write_text(
                "Decoder PROC\n mov eax, 1\nDecoder ENDP\nSharedLabel:\n ret\n",
                encoding="utf-8",
            )

            discovered = discover_source_files([str(temp_dir)], max_files=20)
            c_family_files = [
                item for item in discovered["files"]
                if item["metadata"]["parser_key"] == "c_family"
            ]
            c_family._load_tree_sitter_language_pack.cache_clear()
            c_family._get_tree_sitter_parser.cache_clear()
            parsed = c_family.parse_c_family_corpus(c_family_files, bundle_max_chars=6000)

        symbol_names = {item["metadata"]["symbol_name"] for item in parsed["units"]}
        self.assertIn("demo::run", symbol_names)
        self.assertIn("demo::Widget", symbol_names)
        self.assertIn("MAX_VALUE", symbol_names)
        self.assertIn("demo::DWORD", symbol_names)
        self.assertIn("Decoder", symbol_names)
        self.assertIn("SharedLabel", symbol_names)
        self.assertTrue(parsed["units"])
        self.assertTrue(all(item["metadata"]["parser_mode"] == "tree_sitter" for item in parsed["units"]))
        bundle = parsed["bundles"][0]
        self.assertEqual(bundle["metadata"]["parser_mode"], "tree_sitter")
        self.assertEqual(bundle["metadata"]["parser_modes"][bundle["metadata"]["primary_file"]], "tree_sitter")

    def test_article_parser_extracts_html_snippet_with_context(self) -> None:
        from scripts.utils.discovery import discover_source_files
        from scripts.utils.parsers.html import parse_article_corpus

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            html_path = temp_dir / "guide.html"
            html_path.write_text(
                "<html><head><title>Guide</title></head><body>"
                "<h2>Setup</h2>"
                "<p>Compile the example with your normal toolchain.</p>"
                "<pre><code>#include <stdio.h>\nint main() { return 0; }</code></pre>"
                "<p>This program returns success.</p>"
                "</body></html>",
                encoding="utf-8",
            )

            discovered = discover_source_files([str(html_path)], max_files=10)
            parsed = parse_article_corpus(discovered["files"], bundle_max_chars=4000)

        self.assertEqual(len(parsed["bundles"]), 1)
        bundle = parsed["bundles"][0]
        self.assertEqual(bundle["kind"], "article_snippet_context")
        self.assertIn("Compile the example", bundle["content"])
        self.assertIn("This program returns success", bundle["content"])
        self.assertEqual(bundle["metadata"]["heading"], "Setup")
        self.assertEqual(bundle["metadata"]["snippet_language"], "c_cpp")

    def test_ingest_script_writes_artifacts_and_imports_structured_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            source_dir = temp_dir / "repo"
            source_dir.mkdir()
            db_path = temp_dir / "state.sqlite"
            output_dir = temp_dir / "ingest_output"

            (source_dir / "sample.cpp").write_text(
                '#include "sample.h"\nint meaning(void) { return 42; }\n',
                encoding="utf-8",
            )
            (source_dir / "sample.h").write_text(
                "int meaning(void);\n",
                encoding="utf-8",
            )
            (source_dir / "notes.html").write_text(
                "<html><body><h1>Meaning</h1><p>Use the helper below.</p>"
                "<pre>int meaning(void);</pre><p>The declaration is shared.</p>"
                "</body></html>",
                encoding="utf-8",
            )

            result = run_script(
                "scripts/ingest.py",
                "--paths", str(source_dir),
                "--output-dir", str(output_dir),
                "--db", str(db_path),
                "--tool-context", "codex",
                "--bundle-max-chars", "5000",
            )
            summary = json.loads(result.stdout)

            self.assertEqual(summary["counts"]["files"], 3)
            self.assertGreaterEqual(summary["counts"]["bundles"], 2)
            self.assertGreaterEqual(summary["counts"]["drafts"], 2)
            self.assertEqual(summary["import"]["failed"], 0)
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "files.jsonl").exists())
            self.assertTrue((output_dir / "bundles.jsonl").exists())
            self.assertTrue((output_dir / "drafts.jsonl").exists())

            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT COUNT(*), MIN(source_type), MAX(source_type) FROM records"
                ).fetchone()
            finally:
                conn.close()

            self.assertGreaterEqual(rows[0], 2)
            self.assertEqual(rows[1], "structured_source")
            self.assertEqual(rows[2], "structured_source")


