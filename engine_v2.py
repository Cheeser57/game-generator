from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import product
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import unified_planning as up
from unified_planning.engines import UPSequentialSimulator
from unified_planning.io import PDDLReader
from unified_planning.shortcuts import OneshotPlanner


up.shortcuts.get_environment().credits_stream = None


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass(frozen=True)
class QuestBundle:
    level: int
    folder: Path
    problem_file: Path
    misc_file: Path
    meta_file: Path | None = None
    meta: dict[str, Any] | None = None


class QuestGame:
    def __init__(
        self,
        domain_file: str | Path,
        problem_file: str | Path,
        misc_file: str | Path,
        quest_meta: dict[str, Any] | None = None,
    ):
        self.domain_file = Path(domain_file)
        self.problem_file = Path(problem_file)
        self.misc_file = Path(misc_file)
        self.quest_meta = quest_meta or {}

        self.reader = PDDLReader()
        self.problem = self.reader.parse_problem(
            str(self.domain_file),
            str(self.problem_file),
        )

        self.sim = UPSequentialSimulator(self.problem)
        self.state = self.sim.get_initial_state()

        with self.misc_file.open("r", encoding="utf-8") as handle:
            self.misc = json.load(handle)

        self.objects = {str(obj): obj for obj in self.problem.all_objects}
        self.fluents = {fluent.name: fluent for fluent in self.problem.fluents}

    def fluent_value(self, fluent_name: str, *obj_names: str) -> bool:
        fluent = self.fluents.get(fluent_name)
        if fluent is None:
            return False

        args = [self.objects[name] for name in obj_names if name in self.objects]
        expression = self.problem.environment.expression_manager.FluentExp(fluent, args)
        value = self.state.get_value(expression)
        return value.is_true() if value is not None else False

    def applicable_actions(self):
        try:
            return list(self.sim.get_applicable_actions(self.state))
        except ValueError:
            return self._manual_applicable_actions()

    def _manual_applicable_actions(self):
        grounded_actions = []
        all_objects = list(self.problem.all_objects)

        for action in self.problem.actions:
            candidates: list[list[Any]] = []
            for parameter in action.parameters:
                compatible = [
                    obj for obj in all_objects
                    if obj.type == parameter.type or str(obj.type) == str(parameter.type)
                ]
                if not compatible:
                    candidates = []
                    break
                candidates.append(compatible)

            for params in product(*candidates) if candidates else []:
                if self.sim.is_applicable(self.state, action, params):
                    grounded_actions.append((action, params))

        return grounded_actions

    def _goal_expressions(self) -> list[Any]:
        raw_goal = getattr(self.problem, "goals", None) or getattr(self.problem, "goal", None)
        if not raw_goal:
            return []

        if not isinstance(raw_goal, (list, tuple, set)):
            raw_goal = [raw_goal]

        flattened: list[Any] = []

        def walk(expr: Any) -> None:
            if expr is None:
                return

            if isinstance(expr, (list, tuple, set)):
                for child in expr:
                    walk(child)
                return

            children = None
            for attr in ("args", "operands", "children"):
                value = getattr(expr, attr, None)
                if value:
                    children = list(value)
                    break

            if children and str(expr).lstrip().lower().startswith("(and"):
                for child in children:
                    walk(child)
                return

            flattened.append(expr)

        for goal in raw_goal:
            walk(goal)

        return flattened

    def is_won(self) -> bool:
        if hasattr(self.sim, "is_goal"):
            try:
                return bool(self.sim.is_goal(self.state))
            except Exception:
                pass

        goal_expressions = self._goal_expressions()
        if not goal_expressions:
            return self.fluent_value("game-won")

        for goal in goal_expressions:
            try:
                value = self.state.get_value(goal)
            except Exception:
                return False
            if value is None or not value.is_true():
                return False

        return True

    def current_location(self) -> str | None:
        for room_name in self.misc.get("locations", {}):
            if self.fluent_value("at", "hero", room_name):
                return room_name
        return None

    def render_header(self) -> None:
        title = self.quest_meta.get("title")
        phase = self.quest_meta.get("phase")
        level = self.quest_meta.get("id")
        narrative_goal = self.quest_meta.get("narrative_goal")

        print()
        print("=" * 60)
        if title:
            if isinstance(level, int):
                print(f"  Level {level:02d}: {title}")
            else:
                print(f"  {title}")
        else:
            print("  New quest")
        if narrative_goal:
            print(f"  Goal: {narrative_goal}")
        print("=" * 60)
        print()

    def render_location(self) -> None:
        location = self.current_location()

        if not location:
            print("Unknown location")
            return

        loc_data = self.misc.get("locations", {}).get(location, {})
        loc_name, loc_description = self._display_entry(location, loc_data)

        print()
        print("=" * 50)
        print(loc_name)
        print("=" * 50)
        # print(loc_description)
        print()

    def render_inventory(self) -> None:
        inventory = []

        for item_name in self.misc.get("items", {}):
            if self.fluent_value("has", "hero", item_name):
                inventory.append(item_name)

        print("Inventory:")
        if not inventory:
            print("  (empty)")
        else:
            for item_name in inventory:
                item_data = self.misc.get("items", {}).get(item_name, {})
                item_label, _ = self._display_entry(item_name, item_data)
                print(f"  - {item_label}")

        print()

    def _pretty_params(self, action_def, actual_params) -> dict[str, str]:
        raw_params: dict[str, str] = {}
        for param, value in zip(action_def.parameters, actual_params):
            raw_params[param.name] = str(value)

        pretty_params: dict[str, str] = {}
        for key, value in raw_params.items():
            if value in self.misc.get("locations", {}):
                pretty_params[key] = self._display_entry(value, self.misc["locations"][value])[0]
            elif value in self.misc.get("items", {}):
                pretty_params[key] = self._display_entry(value, self.misc["items"][value])[0]
            elif value in self.misc.get("characters", {}):
                pretty_params[key] = self._display_entry(value, self.misc["characters"][value])[0]
            else:
                pretty_params[key] = value

        pretty_params.setdefault("from", pretty_params.get("from", ""))
        pretty_params.setdefault("to", pretty_params.get("to", ""))
        pretty_params.setdefault("room", pretty_params.get("to") or pretty_params.get("from") or pretty_params.get("r", ""))
        pretty_params.setdefault("r", pretty_params.get("room") or pretty_params.get("r", ""))
        pretty_params.setdefault("location", pretty_params.get("room") or pretty_params.get("r") or pretty_params.get("to") or pretty_params.get("from", ""))
        pretty_params.setdefault("item", pretty_params.get("item", ""))
        pretty_params.setdefault("character", pretty_params.get("c") or pretty_params.get("character", ""))
        pretty_params.setdefault("npc", pretty_params.get("character") or pretty_params.get("npc", ""))
        pretty_params.setdefault("c", pretty_params.get("character") or pretty_params.get("c", ""))
        pretty_params.setdefault("p", pretty_params.get("p", "hero"))

        return pretty_params

    @staticmethod
    def _display_entry(key: str, value: Any) -> tuple[str, str]:
        if isinstance(value, dict):
            name = str(value.get("name", key))
            description = str(value.get("description", ""))
            if not description and isinstance(value.get("dialogue"), dict):
                dialogue = value["dialogue"]
                description = str(dialogue.get("default", ""))
            return name, description

        if isinstance(value, str):
            if " – " in value:
                name, description = value.split(" – ", 1)
                return name.strip(), description.strip()
            return value, ""

        return key, ""

    def action_to_text(self, action_instance) -> str:
        if isinstance(action_instance, tuple):
            action_def, actual_params = action_instance
        else:
            action_def = action_instance.action
            actual_params = action_instance.actual_parameters

        action_name = action_def.name
        template = self.misc.get("action_text", {}).get(action_name, action_name)
        template = re.sub(r"\$\{\{(\w+)\}\}", r"{\1}", template)
        if action_name == "drop" and "{item}" not in template:
            template = "Drop {item}."
        if action_name in {"inspect", "talk"} and ". " in template:
            template = template.split(". ", 1)[0].rstrip()
            if not template.endswith("."):
                template += "."
        pretty_params = self._pretty_params(action_def, actual_params)

        try:
            return template.format_map(SafeFormatDict(pretty_params))
        except Exception as exc:
            return f"[BŁĄD: {exc}] {str(action_instance)}"

    def _action_feedback(self, action_instance) -> str | None:
        if isinstance(action_instance, tuple):
            action_def, actual_params = action_instance
        else:
            action_def = action_instance.action
            actual_params = action_instance.actual_parameters

        action_name = action_def.name
        param_names = [param.name for param in action_def.parameters]
        raw_params = {name: str(value) for name, value in zip(param_names, actual_params)}

        if action_name == "inspect":
            room_name = raw_params.get("r") or raw_params.get("room") or raw_params.get("location")
            if not room_name:
                return None
            loc_data = self.misc.get("locations", {}).get(room_name, {})
            if isinstance(loc_data, dict):
                response = loc_data.get("inspect_text") or loc_data.get("description")
                if response:
                    return str(response)
            elif isinstance(loc_data, str):
                return loc_data
            return None

        if action_name == "talk":
            char_name = raw_params.get("c") or raw_params.get("character") or raw_params.get("npc")
            if not char_name:
                return None
            char_data = self.misc.get("characters", {}).get(char_name, {})
            if isinstance(char_data, dict):
                dialogue = char_data.get("dialogue", {})
                response = char_data.get("talk_text") or (dialogue.get("default") if isinstance(dialogue, dict) else None)
                if response:
                    return str(response)
                if isinstance(dialogue, dict):
                    after_key = dialogue.get("after_key")
                    if after_key:
                        return str(after_key)
            elif isinstance(char_data, str):
                return char_data
            return None

        return None

    def render_actions(self):
        actions = self.applicable_actions()

        print("Available actions:")
        for index, action in enumerate(actions):
            print(f"  {index + 1}. {self.action_to_text(action)}")
        print()

        return actions

    def apply_action(self, action_instance) -> None:
        if isinstance(action_instance, tuple):
            action_def, params = action_instance
            self.state = self.sim.apply(self.state, action_def, params)
        else:
            self.state = self.sim.apply(
                self.state,
                action_instance.action,
                action_instance.actual_parameters,
            )

    def print_action_feedback(self, action_instance) -> None:
        feedback = self._action_feedback(action_instance)
        if feedback:
            print()
            print(feedback)
            print()

    def _build_current_problem(self):
        current_problem = self.problem.clone()
        all_objects = list(self.problem.all_objects)
        em = self.problem.environment.expression_manager

        for fluent in self.problem.fluents:
            param_types = [p.type for p in fluent.signature]

            if not param_types:
                fexp = em.FluentExp(fluent, [])
                value = self.state.get_value(fexp)
                if value is not None:
                    current_problem.set_initial_value(fexp, value)
            else:
                compatible_per_param = [
                    [obj for obj in all_objects
                     if obj.type == ptype or str(obj.type) == str(ptype)]
                    for ptype in param_types
                ]
                if all(compatible_per_param):
                    for combo in product(*compatible_per_param):
                        fexp = em.FluentExp(fluent, list(combo))
                        value = self.state.get_value(fexp)
                        if value is not None:
                            current_problem.set_initial_value(fexp, value)

        return current_problem

    def planner_hint(self) -> None:
        print("\n[Looking for a hint...]\n")

        with OneshotPlanner(name="fast-downward") as planner:
            result = planner.solve(self._build_current_problem())

            if result.plan is None or not result.plan.actions:
                print("No plan.")
                return

            first = result.plan.actions[0]
            print("Suggestion:")
            print(self.action_to_text(first))
            print()

    def run(self) -> str:
        self.render_header()

        while True:
            if self.is_won():
                print()
                print("=" * 50)
                print("  CONGRATULATIONS! You completed this quest!")
                print("=" * 50)
                print()
                return "finished"

            self.render_location()
            self.render_inventory()
            actions = self.render_actions() 

            print("0. Quit game")
            print("r. Restart level")
            print("h. Hint")
            print()

            choice = input("> ").strip().lower()

            if choice == "0":
                return "quit"

            if choice == "r":
                print("\n[Level restart]\n")
                return "restart"

            if choice == "h":
                self.planner_hint()
                continue

            try:
                index = int(choice) - 1
                if index < 0 or index >= len(actions):
                    print("Invalid choice")
                    continue

                selected_action = actions[index]
                self.apply_action(selected_action)
                self.print_action_feedback(selected_action)
            except ValueError:
                print("Invalid command")


class SequentialQuestRunner:
    def __init__(
        self,
        quest_folder: str | Path = "quests",
        domain_file: str | Path = "domain.pddl",
        start_level: int = 1,
    ):
        self.quest_folder = Path(quest_folder)
        self.domain_file = Path(domain_file)
        self.start_level = start_level

    @staticmethod
    def _quest_level_from_dir(path: Path) -> int | None:
        match = re.fullmatch(r"Quest_(\d+)", path.name)
        return int(match.group(1)) if match else None

    def discover_quests(self) -> list[QuestBundle]:
        bundles: list[QuestBundle] = []

        for quest_dir in sorted(self.quest_folder.glob("Quest_*")):
            if not quest_dir.is_dir():
                continue

            level = self._quest_level_from_dir(quest_dir)
            if level is None or level < self.start_level:
                continue

            problem_file = quest_dir / "problem.pddl"
            misc_file = quest_dir / "misc.json"
            meta_file = quest_dir / "quest_meta.json"

            if not problem_file.exists() or not misc_file.exists():
                continue

            meta = None
            if meta_file.exists():
                with meta_file.open("r", encoding="utf-8") as handle:
                    meta = json.load(handle)

            bundles.append(
                QuestBundle(
                    level=level,
                    folder=quest_dir,
                    problem_file=problem_file,
                    misc_file=misc_file,
                    meta_file=meta_file if meta_file.exists() else None,
                    meta=meta,
                )
            )

        bundles.sort(key=lambda bundle: bundle.level)
        return bundles

    def run(self) -> None:
        if not self.domain_file.exists():
            print(f"[ERROR] Domain file not found: {self.domain_file}")
            sys.exit(1)

        if not self.quest_folder.exists():
            print(f"[ERROR] Quest folder not found: {self.quest_folder}")
            sys.exit(1)

        quests = self.discover_quests()
        if not quests:
            print(f"[ERROR] No quests to run in: {self.quest_folder}")
            sys.exit(1)

        print()
        print("#" * 60)
        print("  SEQUENTIAL QUEST ENGINE")
        print("#" * 60)
        print(f"  Quest folder:   {self.quest_folder.resolve()}")
        print(f"  Domain:         {self.domain_file.resolve()}")
        print(f"  Start level:    {self.start_level}")
        print(f"  Available quests: {len(quests)}")
        print("#" * 60)

        quest_map = {quest.level: quest for quest in quests}
        current_level = self.start_level
        started = False

        while current_level in quest_map:
            quest = quest_map[current_level]
            started = True
            meta = quest.meta or {}
            title = meta.get("title") or quest.folder.name

            print()
            print("=" * 60)
            print(f"  Uruchamianie questu {quest.level:02d}: {title}")
            if meta.get("phase"):
                print(f"  Phase: {meta['phase']}")
            print(f"  Folder: {quest.folder}")
            print("=" * 60)

            while True:
                game = QuestGame(self.domain_file, quest.problem_file, quest.misc_file, meta)
                outcome = game.run()

                if outcome == "restart":
                    print()
                    print(f"  Restartuję quest {quest.level:02d}...")
                    continue

                if outcome == "quit":
                    print()
                    print("Game interrupted before completing the remaining quests.")
                    return

                current_level += 1
                break

        if not started:
            print(f"[ERROR] Start quest not found: Quest_{self.start_level:02d}")
            sys.exit(1)

        if quest_map and current_level <= max(quest_map):
            print()
            print(f"No quest found at level {current_level:02d}. Stopping the sequence.")
            return

        print()
        print("#" * 60)
        print("  Completed all available quests from the selected start level")
        print("#" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sekwencyjny engine questów PDDL generowanych przez quest_generator.py"
    )
    parser.add_argument(
        "--quest-folder",
        default="quests",
        help="Folder z questami (domyślnie quests)",
    )
    parser.add_argument(
        "--domain-file",
        default="domain.pddl",
        help="Plik domeny PDDL (domyślnie domain.pddl)",
    )
    parser.add_argument(
        "--start-level",
        type=int,
        default=1,
        help="Numer poziomu, od którego rozpocząć grę (domyślnie 1)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runner = SequentialQuestRunner(
        quest_folder=args.quest_folder,
        domain_file=args.domain_file,
        start_level=args.start_level,
    )
    runner.run()


if __name__ == "__main__":
    main()