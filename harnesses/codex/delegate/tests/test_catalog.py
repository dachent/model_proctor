"""Behavioral contract tests for the fixture-driven Codex catalog boundary."""
import json
import sys
import tempfile
import unittest
from pathlib import Path


_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

from catalog import (  # noqa: E402
    CatalogContractError,
    load_local_config,
    parse_catalog,
    resolve_selection,
)


CATALOG_PAYLOAD = {
    "nextCursor": "opaque-next-page",
    "data": [
        {
            "description": "Fast Codex worker.",
            "displayName": "GPT-5.6 Luna",
            "hidden": False,
            "id": "gpt-5.6-luna",
            "isDefault": False,
            "model": "gpt-5.6-luna",
            "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [
                {"description": "Low", "reasoningEffort": "low"},
                {"description": "Medium", "reasoningEffort": "medium"},
                {"description": "High", "reasoningEffort": "high"},
                {"description": "Maximum", "reasoningEffort": "max"},
            ],
        },
        {
            "description": "Balanced Codex worker.",
            "displayName": "GPT-5.6 Terra",
            "hidden": False,
            "id": "gpt-5.6-terra",
            "isDefault": True,
            "model": "gpt-5.6-terra",
            "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [
                {"description": "Low", "reasoningEffort": "low"},
                {"description": "Medium", "reasoningEffort": "medium"},
                {"description": "High", "reasoningEffort": "high"},
                {"description": "Maximum", "reasoningEffort": "max"},
                {"description": "Ultra", "reasoningEffort": "ultra"},
            ],
        },
        {
            "description": "Highest-capability Codex worker.",
            "displayName": "GPT-6 Astra",
            "hidden": False,
            "id": "gpt-6-astra",
            "isDefault": False,
            "model": "gpt-6-astra",
            "defaultReasoningEffort": "low",
            "supportedReasoningEfforts": [
                {"description": "Low", "reasoningEffort": "low"},
                {"description": "Medium", "reasoningEffort": "medium"},
                {"description": "High", "reasoningEffort": "high"},
                {"description": "Maximum", "reasoningEffort": "max"},
                {"description": "Ultra", "reasoningEffort": "ultra"},
            ],
        },
    ]
}

PRESETS = {
    "schema": 1,
    "presets": {
        "luna": {"model": "gpt-5.6-luna", "effort": "medium"},
        "terra": {"model": "gpt-5.6-terra", "effort": "high"},
        "astra": {"model": "gpt-6-astra", "effort": "max"},
    },
}


class CatalogAndPresetContract(unittest.TestCase):
    def setUp(self):
        self.catalog = parse_catalog(CATALOG_PAYLOAD)
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "local-presets.json"
        self.config_path.write_text(json.dumps(PRESETS), encoding="utf-8")
        self.config = load_local_config(self.config_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_presets_resolve_to_the_fixture_table(self):
        for preset, model, effort in (
            ("luna", "gpt-5.6-luna", "medium"),
            ("terra", "gpt-5.6-terra", "high"),
            ("astra", "gpt-6-astra", "max"),
        ):
            with self.subTest(preset=preset):
                selected = resolve_selection(self.catalog, self.config, preset=preset)
                self.assertEqual((selected.model, selected.effort), (model, effort))
                self.assertEqual(selected.catalog_proof.id, model)

    def test_direct_model_uses_the_live_catalog_default_effort(self):
        selected = resolve_selection(
            self.catalog, self.config, model="gpt-5.6-luna"
        )
        self.assertEqual((selected.model, selected.effort),
                         ("gpt-5.6-luna", "medium"))

    def test_direct_astra_uses_its_live_catalog_default_effort(self):
        selected = resolve_selection(
            self.catalog, self.config, model="gpt-6-astra"
        )
        self.assertEqual((selected.model, selected.effort),
                         ("gpt-6-astra", "low"))

    def test_supported_explicit_effort_overrides_a_preset(self):
        selected = resolve_selection(
            self.catalog, self.config, preset="terra", effort="ultra"
        )
        self.assertEqual((selected.model, selected.effort),
                         ("gpt-5.6-terra", "ultra"))

    def test_unsupported_explicit_effort_refuses_even_for_a_named_preset(self):
        with self.assertRaises(CatalogContractError):
            resolve_selection(self.catalog, self.config, preset="luna", effort="ultra")

    def test_catalog_and_config_contract_violations_refuse(self):
        malformed_catalogs = (
            {},
            {"data": "not-an-array"},
            {"data": [{"id": "missing-fields"}]},
            {"data": [CATALOG_PAYLOAD["data"][0], CATALOG_PAYLOAD["data"][0]]},
        )
        for payload in malformed_catalogs:
            with self.subTest(catalog=payload):
                with self.assertRaises(CatalogContractError):
                    parse_catalog(payload)

        malformed_configs = (
            "not json",
            json.dumps({"schema": 2, "presets": {}}),
            json.dumps({"schema": True, "presets": {}}),
            json.dumps({"schema": 1.0, "presets": {}}),
            json.dumps({"schema": 1, "presets": {"luna": {"model": "x"}}}),
            '{"schema": 1, "presets": {"luna": {"model": "x", "effort": "low"}, '
            '"luna": {"model": "y", "effort": "high"}}}',
            json.dumps({"schema": 1, "presets": {"": {"model": "x", "effort": "low"}}}),
        )
        for raw in malformed_configs:
            with self.subTest(config=raw):
                self.config_path.write_text(raw, encoding="utf-8")
                with self.assertRaises(CatalogContractError):
                    load_local_config(self.config_path)
        with self.assertRaises(CatalogContractError):
            load_local_config(self.config_path.with_name("absent.json"))

    def test_invalid_selection_inputs_refuse(self):
        invalid = (
            {},
            {"model": "gpt-5.6-luna", "preset": "luna"},
            {"model": "unavailable"},
            {"preset": "not-configured"},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(CatalogContractError):
                    resolve_selection(self.catalog, self.config, **kwargs)

    def test_selection_is_immutable_and_contains_only_catalog_proof(self):
        selected = resolve_selection(self.catalog, self.config, preset="astra")
        self.assertEqual(
            set(selected.__dataclass_fields__), {"model", "effort", "catalog_proof"}
        )
        self.assertEqual(
            set(selected.catalog_proof.__dataclass_fields__),
            {"id", "default_reasoning_effort", "supported_reasoning_efforts"},
        )
        with self.assertRaises((AttributeError, TypeError)):
            selected.effort = "low"


if __name__ == "__main__":
    unittest.main()
