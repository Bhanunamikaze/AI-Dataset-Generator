from __future__ import annotations

import unittest

from tests.pipeline_canonical import CanonicalNormalizationTests
from tests.pipeline_scripts import PipelineScriptTests
from tests.pipeline_additional import AdditionalCoverageTests
from tests.pipeline_ingest_collect import CollectorTests

__all__ = [
    "CanonicalNormalizationTests",
    "PipelineScriptTests",
    "AdditionalCoverageTests",
    "CollectorTests",
]

if __name__ == "__main__":
    unittest.main()
