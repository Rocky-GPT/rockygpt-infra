"""Destination safety checks for the opt-in local campus snapshot loader."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clone-campus-for-dev.py"
SPEC = importlib.util.spec_from_file_location("clone_campus_for_dev", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOADER)


class LocalTargetTests(unittest.TestCase):
    def test_explicit_loopback_candidate_is_accepted(self):
        parts = LOADER.local_target(
            "postgresql://postgres@127.0.0.1:55434/rockygpt_profiles_dev_test"
        )
        self.assertEqual(parts["host"], "127.0.0.1")
        self.assertEqual(parts["port"], "55434")
        self.assertEqual(parts["dbname"], "rockygpt_profiles_dev_test")
        self.assertEqual(parts["hostaddr"], "127.0.0.1")
        self.assertEqual(parts["sslmode"], "disable")

    def test_shared_remote_or_unscoped_database_is_rejected(self):
        for target in (
            "postgresql://postgres@db.example:55434/rockygpt_profiles_dev_test",
            "postgresql://postgres@localhost:55434/rockygpt_profiles_dev_test",
            "postgresql://postgres@127.0.0.1:55434/neondb",
            "postgresql://postgres@127.0.0.1/rockygpt_profiles_dev_test",
            "postgresql://postgres@127.0.0.1:55434/rockygpt_profiles_dev_",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                LOADER.local_target(target)

    def test_destination_overrides_are_rejected(self):
        base = "host=127.0.0.1 port=55434 dbname=rockygpt_profiles_dev_test "
        for override in (
            "hostaddr=192.0.2.20",
            "service=shared",
            "options='-c search_path=public'",
            "sslmode=require",
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                LOADER.local_target(base + override)

    def test_invalid_port_is_rejected(self):
        for port in ("0", "65536", "-1", "5432,5433"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                LOADER.local_target(
                    f"host=127.0.0.1 port={port} dbname=rockygpt_profiles_dev_test"
                )

    def test_destination_environment_overrides_are_rejected(self):
        for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "override"}):
                with self.assertRaises(ValueError):
                    LOADER.local_target(
                        "postgresql://postgres@127.0.0.1:55434/rockygpt_profiles_dev_test"
                    )

    def test_snapshot_allowlist_excludes_private_tables(self):
        self.assertTrue({"campus_contacts", "campus_hours", "release_artifacts"} <= set(LOADER.TABLES))
        self.assertFalse({"chat_logs", "feedback", "operations", "turns", "reservations"} & set(LOADER.TABLES))


class ArtifactBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.artifacts = {
            "campus-identities": {"schema_version": 1, "entities": [{
                "kind": "program", "links": [{"collection": "programs", "source_record_keys": ["program:1"]}],
                "relationships": [{"type": "convener", "evidence": [{
                    "field": "customFields.rJQmj", "source_url": "https://catalog.ramapo.edu/programs/1"}]}],
            }]},
            "campus-identity-coverage": {"identity_count": 1, "identities_by_kind": {"program": 1},
                "linked_records": {"programs": 1}, "relationships": {"convener": 1}, "unresolved": []},
            "catalog-conveners": {"collected_at": "2026-07-19T12:42:34.365Z", "source_url": "https://catalog.ramapo.edu",
                "programs": [{"catalogUrl": "https://catalog.ramapo.edu/programs/1", "customFields": {"rJQmj": "<a>Professor</a>"}}]},
        }
        self.write_artifacts()

    def write_artifacts(self):
        for key, value in self.artifacts.items():
            (self.root / f"{key}.json").write_text(json.dumps(value) + "\n")

    def test_course_identities_must_keep_the_preserved_derivation(self):
        code = "CMPS 147"
        courses = {"schema_version": 1, "derivation": "fixture", "courses": [{
            "id": "39b6f485-63cd-5e73-bce1-d1815efbc785", "source_key": "academic-programs",
            "source_record_key": code, "name": None}]}
        self.artifacts["catalog-course-identities"] = courses
        self.write_artifacts()
        artifacts, hashes = LOADER.load_artifacts(None, self.root)
        self.assertIn("catalog-course-identities", hashes)
        courses["courses"][0]["id"] = "00000000-0000-5000-8000-000000000000"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "preserved course ID derivation"):
            LOADER.load_artifacts(None, self.root)

    def test_requirement_edges_must_stay_inside_the_bundle(self):
        program = "11111111-1111-4111-8111-111111111111"
        course = LOADER.course_identity_id("academic-programs", "CMPS 147")
        self.artifacts["campus-identities"]["entities"][0]["id"] = program
        self.artifacts["catalog-course-identities"] = {"schema_version": 1, "derivation": "fixture", "courses": [
            {"id": course, "source_key": "academic-programs", "source_record_key": "CMPS 147", "name": None}]}
        groups = {"schema_version": 1, "groups": [{"id": "group-1"}], "edges": [
            {"type": "requirement_group", "source": {"entity_id": program}, "target": {"record_id": "group-1"}},
            {"type": "requirement_option", "source": {"record_id": "group-1"}, "target": {"entity_id": course}},
        ]}
        self.artifacts["program-requirement-groups"] = groups
        self.write_artifacts()
        LOADER.load_artifacts(None, self.root)
        groups["edges"][1]["target"]["entity_id"] = "22222222-2222-4222-8222-222222222222"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "outside the compiled programs, groups or courses"):
            LOADER.load_artifacts(None, self.root)
        groups["edges"][1]["target"]["entity_id"] = course
        del self.artifacts["catalog-course-identities"]
        (self.root / "catalog-course-identities.json").unlink()
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "need the matching catalog-course-identities"):
            LOADER.load_artifacts(None, self.root)

    def test_program_faculty_listings_need_their_own_catalog_field(self):
        entity = self.artifacts["campus-identities"]["entities"][0]
        entity["relationships"].append({"type": "listed_faculty", "evidence": [{
            "field": "customFields.xiQxl", "source_url": "https://catalog.ramapo.edu/programs/1"}]})
        self.artifacts["campus-identity-coverage"]["relationships"]["listed_faculty"] = 1
        self.write_artifacts()
        # The program publishes a Convener field but no Program Faculty field.
        with self.assertRaisesRegex(ValueError, "Program faculty relationships do not match"):
            LOADER.load_artifacts(None, self.root)
        self.artifacts["catalog-conveners"]["programs"][0]["customFields"]["xiQxl"] = "<a>Professor</a>"
        self.write_artifacts()
        LOADER.load_artifacts(None, self.root)
        entity["relationships"][1]["evidence"][0]["field"] = "customFields.rJQmj"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "Program faculty relationships do not match"):
            LOADER.load_artifacts(None, self.root)

    def buildings(self):
        return {"schema_version": 1, "map_generated_at": "2026-08-27T16:58:11.587Z",
                "source": {"source_key": "campus-map", "title": "Ramapo Campus Map",
                           "canonical_url": "https://map.ramapo.edu/", "trust_tier": "official_primary",
                           "freshness_sla_hours": 4320, "domain": "map"},
                "buildings": [{"concept3d_id": "1133371", "name": "Academic Building D", "room_prefixes": ["D"]}],
                "unresolved": []}

    def test_room_relationships_need_buildings_from_the_bundle(self):
        building = {"id": "building-d", "kind": "building", "links": [
            {"collection": "buildings", "source_key": "campus-map", "source_record_keys": ["1133371"]}]}
        self.artifacts["campus-identities"]["entities"].append(building)
        self.artifacts["campus-identities"]["entities"][0]["relationships"].append({
            "type": "located_at", "target_entity_id": "building-d", "evidence": [
                {"collection": "contacts", "source_key": "campus-directory", "source_record_key": "office:x", "field": "office"}]})
        coverage = self.artifacts["campus-identity-coverage"]
        coverage.update(identity_count=2, identities_by_kind={"program": 1, "building": 1},
                        linked_records={"programs": 1, "buildings": 1},
                        relationships={"convener": 1, "located_at": 1})
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "need the matching campus-buildings artifact"):
            LOADER.load_artifacts(None, self.root)
        self.artifacts["campus-buildings"] = self.buildings()
        self.write_artifacts()
        artifacts, hashes = LOADER.load_artifacts(None, self.root)
        self.assertIn("campus-buildings", hashes)
        building["links"][0]["source_record_keys"] = ["1133372"]
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "must link to buildings in the campus-buildings artifact"):
            LOADER.load_artifacts(None, self.root)
        building["links"][0]["source_record_keys"] = ["1133371"]
        self.artifacts["campus-identities"]["entities"][0]["relationships"][1]["evidence"][0]["field"] = "name"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "cite a contact's office"):
            LOADER.load_artifacts(None, self.root)

    def test_the_static_map_source_keeps_the_map_collection_time(self):
        source, run = LOADER.static_source_rows(self.buildings(), "abc", "map_generated_at", "buildings")
        self.assertEqual(source["source_key"], "campus-map")
        self.assertEqual(set(source), set(LOADER.SOURCE_FIELDS))
        self.assertEqual((run["status"], run["completed_at"], run["record_count"], run["content_hash"]),
                         ("static", "2026-08-27T16:58:11.587Z", 1, "abc"))

    def test_school_placements_need_schools_from_the_bundle(self):
        school = {"id": "school-snh", "kind": "school", "links": [
            {"collection": "schools", "source_key": "ramapo-schools", "source_record_keys": ["snh"]}]}
        self.artifacts["campus-identities"]["entities"].append(school)
        self.artifacts["campus-identities"]["entities"][0]["relationships"].append({
            "type": "part_of", "target_entity_id": "school-snh", "evidence": [
                {"collection": "programs", "source_key": "academic-programs", "source_record_key": "program:1", "field": "school"}]})
        self.artifacts["campus-identity-coverage"].update(
            identity_count=2, identities_by_kind={"program": 1, "school": 1},
            linked_records={"programs": 1, "schools": 1}, relationships={"convener": 1, "part_of": 1})
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "need the matching campus-schools artifact"):
            LOADER.load_artifacts(None, self.root)
        schools = {"schema_version": 1, "captured_at": "2026-09-23T12:31:43Z",
                   "source": {"source_key": "ramapo-schools", "title": "Ramapo Schools",
                              "canonical_url": "https://www.ramapo.edu/academics/schools/",
                              "trust_tier": "official_primary", "freshness_sla_hours": 4320, "domain": "schools"},
                   "schools": [{"section": "snh", "name": "School of Science, Nursing, and Health"}]}
        self.artifacts["campus-schools"] = schools
        self.write_artifacts()
        LOADER.load_artifacts(None, self.root)
        source, run = LOADER.static_source_rows(schools, "def", "captured_at", "schools")
        self.assertEqual((source["source_key"], run["completed_at"], run["record_count"]),
                         ("ramapo-schools", "2026-09-23T12:31:43Z", 1))
        self.artifacts["campus-identities"]["entities"][0]["relationships"][1]["evidence"][0]["field"] = "name"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "cite a published school field"):
            LOADER.load_artifacts(None, self.root)

    def test_subject_courses_need_subjects_from_the_bundle(self):
        subject = {"id": "subject-cmps", "kind": "subject", "links": [
            {"collection": "subjects", "source_key": "course-subjects", "source_record_keys": ["CMPS"]}],
            "relationships": [{"type": "includes_course", "target_record": {
                "collection": "courses", "source_key": "academic-programs", "source_record_key": "CMPS 147"},
                "evidence": [{"collection": "courses", "source_key": "academic-programs",
                              "source_record_key": "CMPS 147", "field": "code"}]}]}
        self.artifacts["campus-identities"]["entities"].append(subject)
        self.artifacts["campus-identity-coverage"].update(
            identity_count=2, identities_by_kind={"program": 1, "subject": 1},
            linked_records={"programs": 1, "subjects": 1}, relationships={"convener": 1, "includes_course": 1})
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "need the matching course-subjects artifact"):
            LOADER.load_artifacts(None, self.root)
        subjects = {"schema_version": 1, "captured_at": "2026-08-27T14:22:13.103Z",
                    "source": {"source_key": "course-subjects", "title": "Ramapo Catalog Subjects",
                               "canonical_url": "https://catalog.ramapo.edu/", "trust_tier": "official_primary",
                               "freshness_sla_hours": 4320, "domain": "courses"},
                    "subjects": [{"code": "CMPS", "name": "Computer Science"}]}
        self.artifacts["course-subjects"] = subjects
        self.write_artifacts()
        LOADER.load_artifacts(None, self.root)
        source, run = LOADER.static_source_rows(subjects, "ghi", "captured_at", "subjects")
        self.assertEqual((source["source_key"], run["completed_at"], run["record_count"]),
                         ("course-subjects", "2026-08-27T14:22:13.103Z", 1))
        # A course filed under another subject's code is not this subject's course.
        subject["relationships"][0]["target_record"]["source_record_key"] = "MATH 110"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "course's own code under its subject's code"):
            LOADER.load_artifacts(None, self.root)
        subject["relationships"][0]["target_record"]["source_record_key"] = "CMPS 147"
        subject["links"][0]["source_record_keys"] = ["CMPT"]
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "link to one subject in the course-subjects artifact"):
            LOADER.load_artifacts(None, self.root)

    def test_matching_trio_preserves_payloads_and_original_collection_time(self):
        artifacts, hashes = LOADER.load_artifacts(None, self.root)
        self.assertEqual(artifacts, self.artifacts)
        self.assertEqual(set(hashes), set(LOADER.PROFILE_ARTIFACTS))
        for key, digest in hashes.items():
            self.assertEqual(digest, hashlib.sha256((self.root / f"{key}.json").read_bytes()).hexdigest())

    def test_pilot_identity_input_stays_supported(self):
        artifacts, hashes = LOADER.load_artifacts(self.root / "campus-identities.json", None)
        self.assertEqual(artifacts, {"campus-identities": self.artifacts["campus-identities"]})
        self.assertEqual(list(hashes), ["campus-identities"])

    def test_complete_bundle_is_required_for_directory_input(self):
        (self.root / "catalog-conveners.json").unlink()
        with self.assertRaises(FileNotFoundError):
            LOADER.load_artifacts(None, self.root)

    def test_mixed_coverage_is_rejected(self):
        self.artifacts["campus-identity-coverage"]["linked_records"] = {"programs": 2}
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "Coverage artifact"):
            LOADER.load_artifacts(None, self.root)

    def test_mixed_convener_evidence_is_rejected(self):
        self.artifacts["catalog-conveners"]["programs"] = []
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "Convener relationships"):
            LOADER.load_artifacts(None, self.root)

    def add_event_organizer(self):
        row = {"source_key": "archway-events", "source_record_key": "date:Meeting",
               "source_record_id": "01d2cdd4-3245-4a49-a561-947f04f0972b",
               "event_url": "https://archway.ramapo.edu/rsvp_boot?id=123",
               "organizer_url": "https://archway.ramapo.edu/example/",
               "collected_at": "2026-08-20T14:05:00Z",
               "source_url": "https://archway.ramapo.edu/rsvp_boot?id=123"}
        self.artifacts["event-organizers"] = {"schema_version": 1, "events": [row]}
        self.artifacts["campus-identities"]["entities"][0]["relationships"].append({
            "type": "organized_by", "evidence": [{
                "collection": "events", "source_key": row["source_key"],
                "source_record_key": row["source_record_key"],
                "source_record_id": row["source_record_id"], "source_url": row["source_url"],
            }],
        })
        self.artifacts["campus-identity-coverage"]["relationships"]["organized_by"] = 1
        self.write_artifacts()

    def test_optional_organizer_evidence_preserves_actual_capture_time(self):
        self.add_event_organizer()
        artifacts, hashes = LOADER.load_artifacts(None, self.root)
        self.assertEqual(artifacts["event-organizers"], self.artifacts["event-organizers"])
        self.assertIn("event-organizers", hashes)

    def test_organizer_links_require_the_matching_optional_evidence(self):
        self.add_event_organizer()
        (self.root / "event-organizers.json").unlink()
        with self.assertRaisesRegex(ValueError, "organizer relationships"):
            LOADER.load_artifacts(None, self.root)

    def test_colliding_event_keys_cannot_replace_original_row_evidence(self):
        self.add_event_organizer()
        self.artifacts["event-organizers"]["events"][0]["source_record_id"] = "different-original-row"
        self.write_artifacts()
        with self.assertRaisesRegex(ValueError, "organizer relationships"):
            LOADER.load_artifacts(None, self.root)

    def test_organizer_capture_time_is_required_and_timezone_aware(self):
        self.add_event_organizer()
        for value in (None, "", "invalid", "2026-08-20T14:05:00"):
            with self.subTest(value=value):
                self.artifacts["event-organizers"]["events"][0]["collected_at"] = value
                self.write_artifacts()
                with self.assertRaisesRegex(ValueError, "capture time"):
                    LOADER.load_artifacts(None, self.root)

    def test_ambiguous_artifact_options_are_rejected(self):
        for file, directory in ((None, None), (self.root / "campus-identities.json", self.root)):
            with self.subTest(file=file, directory=directory), self.assertRaises(ValueError):
                LOADER.load_artifacts(file, directory)

    def test_expected_source_release_is_checked_without_rewriting_metadata(self):
        dataset = {"version": "v2-example", "activated_at": "2026-09-21T12:00:00Z"}
        self.assertIs(LOADER.expected_source(dataset, "v2-example"), dataset)
        self.assertIs(LOADER.expected_source(dataset, None), dataset)
        with self.assertRaisesRegex(ValueError, "changed"):
            LOADER.expected_source(dataset, "v2-other")
        with self.assertRaisesRegex(ValueError, "no active"):
            LOADER.expected_source(None, None)


if __name__ == "__main__":
    unittest.main()
