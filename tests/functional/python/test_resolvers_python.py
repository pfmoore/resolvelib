from __future__ import print_function

import collections
import json
import operator
import os

import packaging.markers
import packaging.requirements
import packaging.utils
import packaging.version
import pytest

from resolvelib.providers import AbstractProvider
from resolvelib.reporters import BaseReporter
from resolvelib.resolvers import Resolver


Candidate = collections.namedtuple("Candidate", "name version extras")


def _eval_marker(marker, extras=(None,)):
    if not marker:
        return True
    if not isinstance(marker, packaging.markers.Marker):
        marker = packaging.markers.Marker(marker)
    return any(marker.evaluate({"extra": extra}) for extra in extras)


def _iter_resolved(data):
    for k, v in data.items():
        if not isinstance(v, dict):
            v = {"version": v}
        yield k, v


class PythonInputProvider(AbstractProvider):
    def __init__(self, filename):
        with open(filename) as f:
            case_data = json.load(f)

        index_name = os.path.normpath(
            os.path.join(
                filename, "..", "..", "index", case_data["index"] + ".json"
            ),
        )
        with open(index_name) as f:
            self.index = json.load(f)

        self.root_requirements = [
            packaging.requirements.Requirement(r)
            for r in case_data["requested"]
        ]
        self.pinned_versions = {}
        self.expected_resolution = {
            k: packaging.version.parse(v["version"])
            for k, v in _iter_resolved(case_data["resolved"])
            if _eval_marker(v.get("marker"))
        }

    def identify(self, dependency):
        name = packaging.utils.canonicalize_name(dependency.name)
        if dependency.extras:
            return "{}[{}]".format(name, ",".join(sorted(dependency.extras)))
        return name

    def get_preference(self, resolution, candidates, information):
        return len(candidates)

    def _iter_matches(self, requirement):
        name = packaging.utils.canonicalize_name(requirement.name)

        for key, value in self.index[name].items():
            version = packaging.version.parse(key)
            if version not in requirement.specifier:
                continue
            yield Candidate(
                name=requirement.name,
                version=version,
                extras=requirement.extras,
            )

    def find_matches(self, requirement):
        mapping = {c.version: c for c in self._iter_matches(requirement)}
        try:
            version = self.pinned_versions[requirement.name]
        except KeyError:
            return sorted(mapping.values(), key=operator.attrgetter("version"))
        return [mapping.pop(version)]

    def is_satisfied_by(self, requirement, candidate):
        return candidate.version in requirement.specifier

    def _iter_dependencies(self, candidate):
        name = packaging.utils.canonicalize_name(candidate.name)
        if candidate.extras:
            r = "{}=={}".format(name, candidate.version)
            yield packaging.requirements.Requirement(r)
        for r in self.index[name][str(candidate.version)]["dependencies"]:
            requirement = packaging.requirements.Requirement(r)
            if not _eval_marker(requirement.marker, candidate.extras):
                continue
            yield requirement

    def get_dependencies(self, candidate):
        return list(self._iter_dependencies(candidate))


INPUTS_DIR = os.path.abspath(os.path.join(__file__, "..", "inputs"))

CASE_DIR = os.path.join(INPUTS_DIR, "case")

CASE_NAMES = [name for name in os.listdir(CASE_DIR) if name.endswith(".json")]


XFAIL_CASES = {
    "different-extras.json": "Resolver stalled",
    "pyrex-1.9.8.json": "Resolver stalled",
    "same-package-extras.json": "State not cleaned up correctly",
}


@pytest.fixture(
    params=[
        pytest.param(
            os.path.join(CASE_DIR, n),
            marks=pytest.mark.xfail(strict=True, reason=XFAIL_CASES[n]),
        )
        if n in XFAIL_CASES
        else os.path.join(CASE_DIR, n)
        for n in CASE_NAMES
    ],
    ids=[n[:-5] for n in CASE_NAMES],
)
def provider(request):
    return PythonInputProvider(request.param)


class PythonTestReporter(BaseReporter):
    def __init__(self):
        self._indent = 0

    def backtracking(self, candidate):
        self._indent -= 1
        print(" " * self._indent, "Back ", candidate, sep="")

    def pinning(self, candidate):
        print(" " * self._indent, "Pin  ", candidate, sep="")
        self._indent += 1


@pytest.fixture(scope="module")
def reporter():
    return PythonTestReporter()


def _format_resolution(result):
    return {
        identifier: candidate.version
        for identifier, candidate in result.mapping.items()
        if not candidate.extras
    }


def test_resolver(provider, reporter):
    resolver = Resolver(provider, reporter)

    result = resolver.resolve(provider.root_requirements)
    assert _format_resolution(result) == provider.expected_resolution
