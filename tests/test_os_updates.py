"""Gates on unattended operating system updates (docs/os-updates.md).

The host's base file, 50unattended-upgrades, allows the security pockets only
and may be maintained by the university's IT department, so ansible adds a
later file instead of writing it. These tests assert what that file must say,
since none of it is observable until a scanner reports a missed update.
"""

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_ROLE = _REPO / "ansible" / "roles" / "metaseed-hub"
_TEMPLATE = _ROLE / "templates" / "unattended-upgrades.conf.j2"
_DEST = "/etc/apt/apt.conf.d/52metaseed-unattended-upgrades"


def _install_task() -> dict:
    tasks = yaml.safe_load((_ROLE / "tasks" / "main.yml").read_text())
    matches = [
        task
        for task in tasks
        if task.get("ansible.builtin.template", {}).get("src") == _TEMPLATE.name
    ]
    assert len(matches) == 1, "the unattended-upgrades file is not installed exactly once"
    return matches[0]


def test_the_file_is_installed_after_the_base_file() -> None:
    task = _install_task()
    assert task["ansible.builtin.template"]["dest"] == _DEST
    assert set(task["tags"]) >= {"setup", "os-updates"}


def test_ansible_never_writes_the_base_file() -> None:
    """Overwriting 50unattended-upgrades would discard settings someone else maintains."""
    for path in (_ROLE / "tasks").glob("*.yml"):
        assert "50unattended-upgrades" not in path.read_text(), path


def test_noble_updates_are_allowed() -> None:
    """On 261124 all 40 pending packages came from noble-updates, none from noble-security."""
    assert '"${distro_id}:${distro_codename}-updates";' in _TEMPLATE.read_text()


def test_reboots_happen_after_the_daily_upgrade() -> None:
    """apt-daily-upgrade runs between 06:00 and 07:00 host time; a reboot time
    before 07:00 would already be past when a kernel update asks for one."""
    group_vars = yaml.safe_load((_REPO / "ansible" / "group_vars" / "all.yml").read_text())
    text = _TEMPLATE.read_text().replace("{{ os_reboot_time }}", group_vars["os_reboot_time"])
    assert 'Unattended-Upgrade::Automatic-Reboot "true";' in text
    match = re.search(r'Unattended-Upgrade::Automatic-Reboot-Time "(\d\d):(\d\d)";', text)
    assert match, "no reboot time is set"
    assert int(match[1]) >= 7, (
        f"reboot time {match[1]}:{match[2]} is before the upgrade window ends"
    )
