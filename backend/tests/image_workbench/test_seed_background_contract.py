"""Seedream 的种子声明必须生成上游接受的 background 字段。"""

import json
from pathlib import Path

import pytest

from app.core.exceptions import CustomException
from app.modules.image_workbench.config.capability import ImageWorkbenchCapabilitySchema, resolve_params
from app.modules.image_workbench.job.client import ImageUpstreamClient


def _pro_capability() -> ImageWorkbenchCapabilitySchema:
    seed_path = Path(__file__).resolve().parents[2] / "sql" / "image_workbench_model.json"
    models = json.loads(seed_path.read_text(encoding="utf-8"))
    pro = next(item for item in models if item["model_id"] == "doubao-seedream-5-0-pro")
    return ImageWorkbenchCapabilitySchema.model_validate(pro["capability_json"])


def test_seed_background_is_valid_upstream_string_for_alpha_reference() -> None:
    capability = _pro_capability()
    facts = {"reference_count": 1, "reference_has_alpha": True}
    params, _ = resolve_params(capability, submitted={"size": "1K"}, facts=facts)
    body = ImageUpstreamClient.build_body(model="doubao-seedream-5-0-pro", prompt="edit <bbox>290 164 706 278</bbox>", params=params)
    assert body["background"] == "opaque"

    transparent, _ = resolve_params(capability, submitted={"background": "transparent"}, facts=facts)
    assert transparent["background"] == "transparent"
    with pytest.raises(CustomException):
        resolve_params(capability, submitted={"background": False}, facts=facts)


def test_seed_omits_background_without_alpha_reference() -> None:
    params, _ = resolve_params(
        _pro_capability(),
        submitted={},
        facts={"reference_count": 1, "reference_has_alpha": False},
    )
    assert "background" not in params
