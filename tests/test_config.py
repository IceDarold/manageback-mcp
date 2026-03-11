from pathlib import Path

import pytest

from managebac_mcp.config import load_managebac_config
from managebac_mcp.errors import AppError


def test_route_url_building(tmp_path: Path):
    cfg_path = tmp_path / "managebac.yaml"
    cfg_path.write_text(
        """
base_url: https://example.com

auth:
  login_url: /student
  username_env: LOGIN
  password_env: PASS

routes:
  classes_index: /student/classes/my
  class_page: /student/classes/{class_id}
  class_tasks: /student/classes/{class_id}/core_tasks
  task_page: /student/classes/{class_id}/core_tasks/{task_id}
  task_dropbox: /student/classes/{class_id}/core_tasks/{task_id}/dropbox
  cas_index: /student/ib/activity/cas
  cas_experience: /student/ib/activity/cas/{experience_id}
  cas_reflections: /student/ib/activity/cas/{experience_id}/reflections
""",
        encoding="utf-8",
    )

    cfg = load_managebac_config(cfg_path)
    assert cfg.route_url("classes_index") == "https://example.com/student/classes/my"
    assert cfg.route_url("task_dropbox", class_id=7, task_id=8) == "https://example.com/student/classes/7/core_tasks/8/dropbox"


def test_missing_route_params_raises(tmp_path: Path):
    cfg_path = tmp_path / "managebac.yaml"
    cfg_path.write_text(
        """
base_url: https://example.com

auth:
  login_url: /student

routes:
  classes_index: /student/classes/my
  class_page: /student/classes/{class_id}
  class_tasks: /student/classes/{class_id}/core_tasks
  task_page: /student/classes/{class_id}/core_tasks/{task_id}
  task_dropbox: /student/classes/{class_id}/core_tasks/{task_id}/dropbox
  cas_index: /student/ib/activity/cas
  cas_experience: /student/ib/activity/cas/{experience_id}
  cas_reflections: /student/ib/activity/cas/{experience_id}/reflections
""",
        encoding="utf-8",
    )
    cfg = load_managebac_config(cfg_path)
    with pytest.raises(AppError):
        cfg.route_url("task_page", class_id=1)
