"""Pure catalog and local-preset validation for the Codex delegate harness."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, FrozenSet, Mapping, Optional


class CatalogContractError(ValueError):
    """Raised when catalog, local-config, or selection input violates this contract."""


@dataclass(frozen=True)
class ModelRecord:
    id: str
    default_reasoning_effort: str
    supported_reasoning_efforts: FrozenSet[str]


@dataclass(frozen=True)
class Preset:
    model: str
    effort: str


@dataclass(frozen=True)
class LocalConfig:
    presets: Mapping[str, Preset]


@dataclass(frozen=True)
class Selection:
    model: str
    effort: str
    catalog_proof: ModelRecord


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogContractError(f"{label} must be a non-empty string")
    return value


def parse_catalog(payload: Any) -> Mapping[str, ModelRecord]:
    """Validate an app-server ``model/list`` payload into immutable records."""
    if not isinstance(payload, dict) or set(payload) != {"data"}:
        raise CatalogContractError("catalog must be an object containing only data")
    data = payload["data"]
    if not isinstance(data, list):
        raise CatalogContractError("catalog data must be an array")

    records: dict[str, ModelRecord] = {}
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise CatalogContractError(f"catalog record {index} must be an object")
        if set(entry) != {
            "id", "defaultReasoningEffort", "supportedReasoningEfforts"
        }:
            raise CatalogContractError(f"catalog record {index} has invalid fields")
        model_id = _nonempty_string(entry["id"], f"catalog record {index} id")
        default = _nonempty_string(
            entry["defaultReasoningEffort"],
            f"catalog record {index} defaultReasoningEffort",
        )
        supported_raw = entry["supportedReasoningEfforts"]
        if not isinstance(supported_raw, list) or not supported_raw:
            raise CatalogContractError(
                f"catalog record {index} supportedReasoningEfforts must be a non-empty array"
            )
        efforts = set()
        for effort_index, effort_entry in enumerate(supported_raw):
            if not isinstance(effort_entry, dict) or set(effort_entry) != {"reasoningEffort"}:
                raise CatalogContractError(
                    f"catalog record {index} supported effort {effort_index} is malformed"
                )
            effort = _nonempty_string(
                effort_entry["reasoningEffort"],
                f"catalog record {index} supported effort {effort_index}",
            )
            if effort in efforts:
                raise CatalogContractError(f"catalog record {index} has duplicate effort {effort}")
            efforts.add(effort)
        if model_id in records:
            raise CatalogContractError(f"catalog has duplicate model id {model_id}")
        if default not in efforts:
            raise CatalogContractError(
                f"catalog record {index} defaultReasoningEffort is not supported"
            )
        records[model_id] = ModelRecord(model_id, default, frozenset(efforts))
    return MappingProxyType(records)


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogContractError(f"local config has duplicate key {key}")
        result[key] = value
    return result


def load_local_config(path: str | Path) -> LocalConfig:
    """Load an untracked schema-1 local preset file without side effects."""
    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CatalogContractError(f"local config is unreadable: {config_path}") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_json_object_no_duplicates)
    except (json.JSONDecodeError, CatalogContractError) as exc:
        raise CatalogContractError("local config is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "presets"}:
        raise CatalogContractError("local config must contain only schema and presets")
    if payload["schema"] != 1:
        raise CatalogContractError("local config schema must be 1")
    raw_presets = payload["presets"]
    if not isinstance(raw_presets, dict):
        raise CatalogContractError("local config presets must be an object")

    presets: dict[str, Preset] = {}
    for name, raw_preset in raw_presets.items():
        _nonempty_string(name, "preset name")
        if not isinstance(raw_preset, dict) or set(raw_preset) != {"model", "effort"}:
            raise CatalogContractError(f"preset {name} must contain only model and effort")
        presets[name] = Preset(
            _nonempty_string(raw_preset["model"], f"preset {name} model"),
            _nonempty_string(raw_preset["effort"], f"preset {name} effort"),
        )
    return LocalConfig(MappingProxyType(presets))


def resolve_selection(
    catalog: Mapping[str, ModelRecord],
    config: LocalConfig,
    model: Optional[str] = None,
    preset: Optional[str] = None,
    effort: Optional[str] = None,
) -> Selection:
    """Resolve one explicit model or local preset against the supplied live catalog."""
    if (model is None) == (preset is None):
        raise CatalogContractError("exactly one of model or preset is required")
    if not isinstance(catalog, Mapping) or not isinstance(config, LocalConfig):
        raise CatalogContractError("catalog and config must be validated contract objects")
    if effort is not None:
        _nonempty_string(effort, "effort")

    if preset is not None:
        preset_name = _nonempty_string(preset, "preset")
        try:
            configured = config.presets[preset_name]
        except KeyError as exc:
            raise CatalogContractError(f"preset is not configured: {preset_name}") from exc
        model_id = configured.model
        selected_effort = effort if effort is not None else configured.effort
    else:
        model_id = _nonempty_string(model, "model")
        selected_effort = effort

    try:
        proof = catalog[model_id]
    except KeyError as exc:
        raise CatalogContractError(f"model is not in the live catalog: {model_id}") from exc
    if not isinstance(proof, ModelRecord):
        raise CatalogContractError("catalog contains an invalid model record")
    if selected_effort is None:
        selected_effort = proof.default_reasoning_effort
    if selected_effort not in proof.supported_reasoning_efforts:
        raise CatalogContractError(
            f"effort {selected_effort} is not supported by model {model_id}"
        )
    return Selection(model_id, selected_effort, proof)
