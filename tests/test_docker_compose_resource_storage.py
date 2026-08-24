from pathlib import Path

from ruamel.yaml import YAML


def test_compose_pins_data_path_to_the_persistent_volume():
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = YAML(typ="safe").load(compose_path.read_text(encoding="utf-8"))
    service = compose["services"]["kirara-agent"]

    assert service["environment"]["DATA_PATH"] == "/app/data"
    assert "./data:/app/data" in service["volumes"]
