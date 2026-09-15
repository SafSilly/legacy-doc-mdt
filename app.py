from __future__ import annotations

import os
import calendar
import csv
import hashlib
import io
import json
import platform
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from functools import wraps
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from training_questions import CADET_TRAINING_CATEGORIES, SOP_SOURCE_URL


app = Flask(__name__)
app.config["MDT_PREVIEW_SHORTCUTS"] = os.environ.get("MDT_PREVIEW_SHORTCUTS") == "1"
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
app.config["SUPERVISOR_PASSWORD"] = os.environ.get("SUPERVISOR_PASSWORD", "DOC2026!")
app.config["OWNER_USERNAME"] = os.environ.get("OWNER_USERNAME", "209517")
app.config["DATABASE"] = os.environ.get("DATABASE_URL", "/var/data/doc_quiz.db" if Path("/var/data").exists() else "instance/doc_quiz.db")
app.config["COMPLAINTS_URL"] = os.environ.get("COMPLAINTS_URL", "")
app.config["DOC_APPLICATION_URL"] = os.environ.get("DOC_APPLICATION_URL", "")
app.config["APP_TIMEZONE"] = os.environ.get("APP_TIMEZONE", "America/Denver")
app.config["ERT_ROSTER_SHEET_URL"] = os.environ.get(
    "ERT_ROSTER_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1L7OO8475t_Ou9Vfok1OXH8qE6xoBXR3a4ejWy6c7HIo/gviz/tq?tqx=out:csv&gid=1757646467",
)
app.config["FTP_ROSTER_SHEET_URL"] = os.environ.get(
    "FTP_ROSTER_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1L7OO8475t_Ou9Vfok1OXH8qE6xoBXR3a4ejWy6c7HIo/gviz/tq?tqx=out:csv&gid=291895850",
)
app.config["DOC_ROSTER_SHEET_URL"] = os.environ.get(
    "DOC_ROSTER_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1L7OO8475t_Ou9Vfok1OXH8qE6xoBXR3a4ejWy6c7HIo/gviz/tq?tqx=out:csv&gid=0",
)
app.config["ERT_ROSTER_CACHE_SECONDS"] = int(os.environ.get("ERT_ROSTER_CACHE_SECONDS", "300"))
_ERT_ROSTER_CACHE: dict[str, object] = {"fetched_at": 0.0, "synced_at": 0.0, "members": [], "changed_keys": set(), "available": False}
_FTP_ROSTER_CACHE: dict[str, object] = {"fetched_at": 0.0, "synced_at": 0.0, "members": [], "changed_keys": set(), "available": False}
_DOC_ROSTER_CACHE: dict[str, object] = {"fetched_at": 0.0, "synced_at": 0.0, "members": [], "changed_keys": set(), "available": False}
STATIC_ASSET_FILES = tuple(Path('static') / name for name in ('styles.css', 'live-actions.js', 'mdt.css', 'mdt.js', 'mdt-auth.css', 'mdt-auth.js', 'mdt-accounts.js', 'mdt-duty.css', 'mdt-duty.js', 'mdt-profile.css'))
_static_asset_hash = hashlib.sha1()
for _static_asset_path in STATIC_ASSET_FILES:
    _static_asset_hash.update(_static_asset_path.read_bytes())
STATIC_VERSION = os.environ.get("RENDER_GIT_COMMIT") or _static_asset_hash.hexdigest()[:12]


@app.url_defaults
def add_static_asset_version(endpoint, values):
    if endpoint == "static":
        values.setdefault("v", STATIC_VERSION)


@app.after_request
def add_performance_headers(response):
    if request.endpoint == "static":
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    if request.headers.get("X-Requested-With") == "live-panel" and 300 <= response.status_code < 400:
        destination = response.headers.get("Location")
        if destination:
            response.status_code = 204
            response.headers.pop("Location", None)
            response.headers["X-Live-Redirect"] = destination
            response.set_data(b"")
    return response


CERT_QUIZZES = {"corrections-officer", "solo-patrol"}
TRAINING_QUIZZES = {"ten-codes", "full-sop"}
TEST_REQUEST_EXPIRY_MINUTES = 15
ADMIN_ROLES = {"owner", "admin"}
SUPERVISOR_ROLES = {"owner", "admin", "supervisor"}
FTO_ROLES = {"owner", "admin", "supervisor"}
CADET_PANEL_ROLES = {"owner", "admin", "cadet"}
EMPLOYEE_ROLES = {"owner", "admin", "supervisor", "officer", "cadet"}
USER_ROLES = ("cadet", "officer", "supervisor", "admin", "owner")
ERT_DIVISION_RANKS = ("ert_member", "ert_training_officer", "ert_supervisor", "ert_command")
FTP_DIVISION_RANKS = ("ftp_member", "ftp_senior_trainer", "ftp_supervisor", "ftp_command")
DIVISION_RANKS = ("", *ERT_DIVISION_RANKS, *FTP_DIVISION_RANKS)
DIVISION_RANK_LABELS = {
    "": "None",
    "ert_member": "ERT · Member",
    "ert_training_officer": "ERT · Training Officer",
    "ert_supervisor": "ERT · Supervisor",
    "ert_command": "ERT · Command",
    "ftp_member": "FTP · Member",
    "ftp_senior_trainer": "FTP · Senior Trainer",
    "ftp_supervisor": "FTP · Supervisor",
    "ftp_command": "FTP · Command",
}
DIVISION_ROLE_GROUPS = (
    {"key": "ert", "title": "Emergency Response Team", "short": "ERT", "ranks": ERT_DIVISION_RANKS},
    {"key": "ftp", "title": "Field Training Program", "short": "FTP", "ranks": FTP_DIVISION_RANKS},
)
PROFILE_CERTIFICATION_COLUMNS = (
    ("Beanbag", "Beanbag Certification"),
    ("Revolver", "Revolver Certification"),
    ("SMG", "SMG Certification"),
    ("Rifle", "Rifle Certification"),
    ("Shotgun", "Shotgun Certification"),
)
DIVISION_APPLICATION_RANKS = {"ert_supervisor", "ert_command", "ftp_supervisor", "ftp_command"}
DIVISION_APPLICATION_REVIEW_RANKS = {
    "ert": {"ert_supervisor", "ert_command"},
    "ftp": {"ftp_supervisor", "ftp_command"},
}
DIVISION_TRAINING_DEPARTMENTS = (
    "DOC",
    "SWAT",
    "State",
    "SASP",
    "BCSO",
    "EMS",
    "DOJ",
    "Other",
)
ADMIN_TAB_IDS = {
    "accounts-tab",
    "builtin-tab",
    "add-custom-tab",
    "custom-tab",
    "complaints-tab",
}
DEFAULT_FTO_OPTIONS = (
    "[S-202] Marshall Ford",
    "[S-212] Ginevra Manuzza",
    "[S-250] Patrick Frailey",
    "[S-267] Esme Carlie",
    "[SO-357] Beau Charles",
    "[LT-123] Phillipe Gowin",
    "[LT-122] Timmy Jefferson",
    "[LT-120] Tom Edwards",
)
STAFF_SECTIONS = (
    {
        "title": "Wardens",
        "members": (
            {"name": "Eddie Newton", "rank": "Warden", "image": "staff/eddie-newton.png"},
            {"name": "Ben Pickleson", "rank": "Deputy Warden", "image": "staff/ben-pickleson.png"},
        ),
    },
    {
        "title": "High Command",
        "members": (
            {"name": "Tom Edwards", "rank": "Captain", "image": "staff/tom-edwards.png"},
        ),
    },
    {
        "title": "Low Command",
        "members": (
            {"name": "Timmy Jefferson", "rank": "Lieutenant", "image": "staff/timmy-jefferson.png"},
        ),
    },
    {
        "title": "Supervisors",
        "members": (
            {"name": "Marshal Ford", "rank": "Sergeant", "image": "staff/marshal-ford.png"},
            {"name": "Esme Carlie", "rank": "Sergeant", "image": "staff/esme-carlie.png"},
        ),
    },
)
DIVISIONS = (
    {
        "slug": "ftp",
        "title": "Field Training Program",
        "short_title": "FTP",
        "image": "divisions/ftp.png",
        "hero": "album/doc-album-6.webp",
        "body": (
            "The Field Training Program (FTP) is a structured, hands-on training process designed to transition cadets from the academy environment into fully functional correctional officers. During this phase, cadets are paired with experienced training officers who provide direct supervision, mentorship, and real-world instruction inside a correctional facility.",
            "The program focuses on developing practical skills, reinforcing policy and procedure, building confidence in daily operations, and exposing cadets to inmate management, security protocols, emergency response, and communication techniques in a controlled setting.",
            "Through continuous evaluation and feedback, the FTP ensures each cadet demonstrates competency, professionalism, and sound decision-making before advancing to solo status.",
        ),
    },
    {
        "slug": "ert",
        "title": "Emergency Response Team",
        "short_title": "ERT",
        "image": "divisions/ert.png",
        "hero": "album/doc-album-3.webp",
        "body": (
            "The Emergency Response Team (ERT) is a specialized unit within the Department of Corrections tasked with responding to high-risk and critical incidents within correctional facilities. This division is composed of highly trained personnel equipped to manage volatile and potentially dangerous situations that exceed the scope of standard operations.",
            "ERT is responsible for handling high-risk inmate transports, in-custody assaults and stabbings, disturbances, and other emergency situations that pose a threat to the safety and security of staff, inmates, and the facility.",
            "Operating with precision and discipline, ERT provides immediate intervention, containment, and control during emergencies while restoring order and minimizing risk.",
        ),
    },
)
RANK_OPTIONS = (
    "Warden",
    "Captain",
    "Lieutenant",
    "Sergeant",
    "Senior Corrections Officer",
    "Corrections Officer",
    "Corrections Cadet",
)
APPLICATION_FORMS = {
    "ftp": {
        "title": "FTP Application - Department of Corrections",
        "short_title": "FTP Application",
        "description": "Field Training Program application for DOC personnel who want to train cadets.",
        "fields": (
            {"name": "discord", "label": "What is your Discord?", "type": "text", "required": True},
            {"name": "full_name", "label": "What is your full name?", "type": "text", "required": True},
            {"name": "callsign", "label": "What is your callsign?", "type": "text", "required": True},
            {"name": "rank", "label": "What is your rank?", "type": "select", "options": RANK_OPTIONS, "required": True},
            {
                "name": "why_join",
                "label": "Why do you want to join the FTP department?",
                "type": "textarea",
                "required": True,
            },
            {
                "name": "good_teacher",
                "label": "Do you think you'd be a good teacher? If so, why?",
                "type": "textarea",
                "required": True,
            },
            {
                "name": "scenario_1",
                "label": "Scenario 1: The cadet you are currently training is rude, stuck-up and doesn't want to listen to you during the training session. What would you do?",
                "type": "textarea",
                "required": False,
            },
            {
                "name": "scenario_2",
                "label": "Scenario 2: You clock on duty to see a cadet messing around with the vehicles in the prison. What would you do?",
                "type": "textarea",
                "required": True,
            },
            {
                "name": "scenario_3",
                "label": "Scenario 3: Your cadet is expressing that they are having difficulties understanding the SOPs. What would you do?",
                "type": "textarea",
                "required": True,
            },
            {
                "name": "scenario_4",
                "label": "Scenario 4: Whilst on a prison transport, your cadet is being openly rude to a police officer involved in the transport. What would you do?",
                "type": "textarea",
                "required": False,
            },
            {
                "name": "trainer_purpose",
                "label": "As a field trainer, you are there to...",
                "type": "radio",
                "required": True,
                "options": (
                    "Mess around and show that I am more superior than anyone else.",
                    "Ensure all cadets coming into the department are properly trained and understand the role they are taking on. As well as this, you are there to ensure the standard of the department is upheld and maintained at all times.",
                    "Look cool in my new uniform.",
                    "Have law enforcement power.",
                ),
            },
            {
                "name": "first_after_interview",
                "label": "What would you do with a cadet first after they finish their interview?",
                "type": "radio",
                "required": True,
                "options": (
                    "Get them set up with their uniform, in-game job and then give them a tour of the prison.",
                    "Get them to clock on duty and leave them alone.",
                    "Tell them to come back later.",
                    "Have a cup of coffee with them.",
                ),
            },
            {
                "name": "after_finish",
                "label": "After you finish with a cadet, you are to...",
                "type": "radio",
                "required": True,
                "options": (
                    "Clock off duty and ignore any sort of paperwork.",
                    "Ensure a cadet report is written up in the #cadet-operations channel.",
                    "Beg for a promotion.",
                    "Bully the cadet.",
                ),
            },
            {
                "name": "law_power",
                "label": "As a field trainer, do you have any sort of law enforcement power?",
                "type": "radio",
                "required": True,
                "options": ("Yes", "No"),
            },
        ),
    },
    "ert": {
        "title": "ERT Application - Department of Corrections",
        "short_title": "ERT Application",
        "description": "Emergency Response Team application for DOC personnel who want to join tactical response.",
        "fields": (
            {"name": "discord", "label": "What is your Discord?", "type": "text", "required": True},
            {"name": "full_name", "label": "What is your full name?", "type": "text", "required": True},
            {"name": "callsign", "label": "What is your callsign?", "type": "text", "required": True},
            {"name": "rank", "label": "What is your rank?", "type": "select", "options": RANK_OPTIONS, "required": True},
            {"name": "why_join", "label": "Why do you want to join the ERT?", "type": "textarea", "required": True},
            {"name": "bring", "label": "What will you bring to the ERT?", "type": "textarea", "required": True},
        ),
    },
}
CADET_PROGRESS_CHECKS = (
    "onboarding_interview",
    "onboarding_setup",
    "onboarding_academy",
    "phase1_step_1",
    "phase1_step_2",
    "phase1_step_3",
    "phase1_step_4",
    "phase1_deescalation_training",
    "phase1_tactical_training",
    "phase2_prison_transport_1",
    "phase2_prison_transport_2",
    "phase2_prison_transport_3",
    "phase2_incident_report_1",
    "phase2_incident_report_2",
    "phase2_riot",
    "phase2_visitation",
    "final_exam_authorized",
)


@app.template_filter("bracket_callsign")
def bracket_callsign(value: object) -> str:
    callsign = str(value or "").strip()
    if not callsign:
        return ""
    if callsign.startswith("[") and callsign.endswith("]"):
        return callsign
    return f"[{callsign.strip('[]')}]"
CADET_PROGRESS_TEXT = ("solo_phase_start", "solo_phase_end")


@dataclass(frozen=True)
class Question:
    prompt: str
    answer: str
    choices: tuple[str, ...]
    category: str
    written: bool = False
    aliases: tuple[str, ...] = ()
    source_key: str = ""


@dataclass(frozen=True)
class Quiz:
    slug: str
    title: str
    summary: str
    categories: tuple[str, ...] | None = None
    include_codes: bool = False
    passing_score: int | None = None
    difficulty: str = "standard"
    max_code_questions: int | None = None
    question_count: int | None = None


CODE_ENTRIES: tuple[tuple[str, str], ...] = (
    ("Code 0", "Game Crash"),
    ("Code 4", "All Clear"),
    ("10-1", "Unable to Copy"),
    ("10-2", "Loud and Clear"),
    ("10-3", "Tactical Communication"),
    ("10-4", "Acknowledgement"),
    ("10-5", "Relay"),
    ("10-6", "Busy, Unless Urgent"),
    ("10-7", "Out of Service"),
    ("10-8", "In Service"),
    ("10-9", "Repeat"),
    ("10-10", "Negative"),
    ("10-13", "Shots Fired"),
    ("10-14", "Officer or Medic Injured"),
    ("10-15", "Inmate"),
    ("10-19", "Enroute to Station/Prison"),
    ("10-20", "Location"),
    ("10-22", "Disregard"),
    ("10-23", "Arrived on Scene"),
    ("10-25", "Report in person"),
    ("10-41", "On Duty"),
    ("10-42", "Off Duty"),
    ("10-43", "Mental Patient"),
    ("10-47", "Injured Person"),
    ("10-50", "Vehicle Accident/Collision"),
    ("10-52", "Police Required"),
    ("10-71", "Active Shooting"),
    ("10-72", "Hostage Situation"),
    ("10-76", "Enroute"),
    ("10-77", "Need Backup (Non-Emergency)"),
    ("10-78", "Need Backup (Emergency)"),
    ("10-79", "ETA"),
    ("10-91", "Transport Unit"),
    ("10-99", "Officer in Distress"),
)


ANSWER_ALIASES: dict[str, tuple[str, ...]] = {
    "Acknowledgement": ("Affirmative", "Affirm", "Copy", "Copy that", "Received", "Understood", "Roger", "Yes"),
    "Unable to Copy": ("Unreadable", "Cannot copy", "Can't copy", "Could not copy", "I cannot hear you"),
    "Loud and Clear": ("Clear", "Readable", "I can hear you", "Copy loud and clear"),
    "Tactical Communication": ("Tac comms", "Tactical comms", "Radio silence", "Keep comms clear"),
    "Relay": ("Pass it on", "Forward the message", "Send the message"),
    "Busy, Unless Urgent": ("Busy", "Unavailable unless urgent", "Occupied unless urgent"),
    "Out of Service": ("Unavailable", "Off service", "Not in service"),
    "In Service": ("Available", "On service", "Back in service"),
    "Repeat": ("Repeat last", "Say again", "Run that back", "Repeat transmission"),
    "Negative": ("No", "Denied", "Not correct"),
    "Shots Fired": ("Shots", "Gunshots", "Gunfire"),
    "Officer or Medic Injured": ("Officer injured", "Medic injured", "Officer down", "Medic down"),
    "Inmate": ("Prisoner", "Convict", "Detainee"),
    "Enroute to Station/Prison": ("En route to station", "Enroute to prison", "Going to prison", "Going to station"),
    "Location": ("Where are you", "Where you at", "Current location"),
    "Disregard": ("Ignore", "Cancel", "Never mind", "Stand down"),
    "Arrived on Scene": ("On scene", "Arrived", "At scene"),
    "Report in person": ("Meet in person", "Come see me", "Report to me"),
    "On Duty": ("Clocked in", "In service for shift", "Starting duty"),
    "Off Duty": ("Clocked out", "Ending duty", "End of duty"),
    "Mental Patient": ("Mental health patient", "Mentally unstable person"),
    "Injured Person": ("Injured individual", "Wounded person", "Hurt person"),
    "Vehicle Accident/Collision": ("Vehicle accident", "Collision", "Car crash", "Crash"),
    "Police Required": ("Need police", "PD required", "Police needed"),
    "Active Shooting": ("Active shooter", "Shooting in progress"),
    "Hostage Situation": ("Hostage", "Hostages", "Person held hostage"),
    "Enroute": ("En route", "On the way", "Responding"),
    "Need Backup (Non-Emergency)": ("Need backup", "Backup non emergency", "Non emergency backup"),
    "Need Backup (Emergency)": ("Need emergency backup", "Emergency backup", "Urgent backup"),
    "ETA": ("Estimated time", "Estimated time of arrival", "Time of arrival"),
    "Transport Unit": ("Transport", "Prisoner transport", "Need transport"),
    "Officer in Distress": ("Officer needs help", "Panic", "Emergency distress", "Officer emergency"),
    "Game Crash": ("Crashed", "My game crashed", "Computer crash"),
    "All Clear": ("Clear", "Scene clear", "Situation clear", "Code four"),
}


ANSWER_KEYWORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "Enroute to Station/Prison": (
        ("to", "prison"),
        ("to", "station"),
        ("going", "prison"),
        ("going", "station"),
        ("enroute", "prison"),
        ("enroute", "station"),
    ),
    "Need Backup (Non-Emergency)": (
        ("backup", "non", "emergency"),
        ("backup", "not", "emergency"),
        ("need", "backup"),
    ),
    "Need Backup (Emergency)": (
        ("backup", "emergency"),
        ("urgent", "backup"),
        ("need", "emergency", "backup"),
    ),
    "Officer or Medic Injured": (
        ("officer", "injured"),
        ("medic", "injured"),
        ("officer", "down"),
        ("medic", "down"),
    ),
    "Vehicle Accident/Collision": (
        ("vehicle", "accident"),
        ("car", "crash"),
        ("collision",),
    ),
    "Officer in Distress": (
        ("officer", "distress"),
        ("officer", "panic"),
        ("officer", "emergency"),
    ),
}


SOP_QUESTIONS: tuple[Question, ...] = (
    Question(
        "What radio frequency does the Department of Corrections operate on?",
        "3.00",
        ("3.00", "1.00", "2.50", "10.00"),
        "Radio",
    ),
    Question(
        "What radio format should be used when relaying information?",
        "[CALLSIGN] > [10-CODE] > [DESCRIPTION]",
        (
            "[CALLSIGN] > [10-CODE] > [DESCRIPTION]",
            "[RANK] > [NAME] > [BADGE NUMBER]",
            "[LOCATION] > [UNIT] > [TIME]",
            "[DESCRIPTION] > [CALLSIGN] > [RANK]",
        ),
        "Radio",
    ),
    Question(
        "Who has the highest level of authoritative power in DOC?",
        "Warden, Deputy Warden, and Assistant Warden",
        (
            "Warden, Deputy Warden, and Assistant Warden",
            "Sergeants only",
            "Senior Corrections Officers",
            "Corrections Cadets",
        ),
        "Command",
    ),
    Question(
        "Which rank is at the bottom of the DOC rank structure?",
        "Corrections Cadet",
        ("Corrections Cadet", "Corrections Officer", "Sergeant", "Senior Corrections Officer"),
        "Command",
    ),
    Question(
        "Who are the day-to-day supervisors for Corrections Officers?",
        "Sergeants",
        ("Sergeants", "Corrections Cadets", "Visitors", "Inmates"),
        "Command",
    ),
    Question(
        "What should an officer do if no supervisor is available and they are unsure whether something is allowed?",
        "Treat the answer as no",
        ("Treat the answer as no", "Do it anyway", "Ask an inmate", "Ignore the SOP"),
        "Rules",
    ),
    Question(
        "What division teaches upcoming cadets the way of a Corrections Officer?",
        "Field Training Program",
        ("Field Training Program", "Emergency Response Team", "Maximum Security", "Department of Justice"),
        "Divisions",
    ),
    Question(
        "Which division handles highly trained threat response inside the prison?",
        "Emergency Response Team",
        ("Emergency Response Team", "Field Training Program", "City Hall Detail", "Court Clerk Program"),
        "Divisions",
    ),
    Question(
        "Which item is required standardized equipment during an active shift?",
        "DOC Badge",
        ("DOC Badge", "SMG", "Rifle", "Personal civilian vehicle"),
        "Equipment",
    ),
    Question(
        "Which vehicle is authorized for Corrections Cadet+?",
        "Prison Bus",
        ("Prison Bus", "2018 Chevy Tahoe only", "Helicopter", "Unmarked cruiser"),
        "Vehicles",
    ),
    Question(
        "Who may carry a .357 Magnum Revolver?",
        "Senior Corrections Officer and above",
        (
            "Senior Corrections Officer and above",
            "Corrections Cadet only",
            "Any inmate trustee",
            "Visitors with ID",
        ),
        "Equipment",
    ),
    Question(
        "When should large high-calibre weapons be stored in the glovebox unless transport is high risk?",
        "When carrying a SIG MCX or H&K 416",
        (
            "When carrying a SIG MCX or H&K 416",
            "When carrying a flashlight",
            "When carrying a radio",
            "When carrying a DOC badge",
        ),
        "Weapons",
    ),
    Question(
        "What is the only listed exception for using lights and sirens?",
        "A prison transport",
        ("A prison transport", "Going to buy food", "Routine city patrol", "Driving to City Hall"),
        "Vehicles",
    ),
    Question(
        "What should be done to all inmates before putting them in a transport vehicle?",
        "Soft cuff them",
        ("Soft cuff them", "Remove all cuffs", "Let them choose a seat freely", "Give them a radio"),
        "Transport",
    ),
    Question(
        "What should DOC do if the transport vehicle is disabled?",
        "Notify PD immediately",
        ("Notify PD immediately", "Walk inmates alone", "Abandon the inmates", "End radio communication"),
        "Transport",
    ),
    Question(
        "When should the bus drive into intake after an active gate situation?",
        "Once the situation is announced Code 4",
        (
            "Once the situation is announced Code 4",
            "Immediately regardless of tailgating",
            "Before contacting PD",
            "Only after all inmates are uncuffed",
        ),
        "Transport",
    ),
    Question(
        "What is Risk Level One?",
        "Regular prison operations with less-than-lethal equipped and weapons holstered",
        (
            "Regular prison operations with less-than-lethal equipped and weapons holstered",
            "A severe riot with lethal weapons authorized",
            "A hostage situation outside prison",
            "A court-only security level",
        ),
        "Risk Levels",
    ),
    Question(
        "What risk level authorizes Beanbag Shotgun engagement?",
        "Risk Level Three",
        ("Risk Level Three", "Risk Level One", "Risk Level Two", "No risk level"),
        "Risk Levels",
    ),
    Question(
        "What risk level is triggered by severe incidents, especially riots and escape attempts?",
        "Risk Level Four",
        ("Risk Level Four", "Risk Level One", "Risk Level Two", "Risk Level Three"),
        "Risk Levels",
    ),
    Question(
        "When may lethal force be used?",
        "When there is an immediate threat to life and no bystander injury risk",
        (
            "When there is an immediate threat to life and no bystander injury risk",
            "Whenever an inmate insults staff",
            "To speed up routine compliance",
            "During every regular patrol",
        ),
        "Use of Force",
    ),
    Question(
        "What must follow any use of lethal force?",
        "An incident report",
        ("An incident report", "A promotion request", "A vehicle inspection", "A radio volume change"),
        "Use of Force",
    ),
    Question(
        "What weapons are allowed inside the prison on normal duty?",
        "Taser and Nightstick",
        ("Taser and Nightstick", "Rifle and SMG", "Pistol only", "No equipment at all"),
        "Weapons",
    ),
    Question(
        "When off duty, what DOC equipment are you allowed to keep on you?",
        "None of it",
        ("None of it", "Tasers only", "Flashlights only", "All equipment if hidden"),
        "Weapons",
    ),
    Question(
        "What must visitors have to enter the prison?",
        "State issued identification",
        ("State issued identification", "A DOC radio", "A weapon permit only", "A prisoner number"),
        "Visitation",
    ),
    Question(
        "What happens if visitors refuse a search?",
        "They are not allowed into the prison",
        (
            "They are not allowed into the prison",
            "They skip the search",
            "They are promoted to escort",
            "They enter through intake",
        ),
        "Visitation",
    ),
    Question(
        "For HUT inmates, who must approve regular visitation first?",
        "FIB/DOJ",
        ("FIB/DOJ", "Any cadet", "The inmate", "A civilian visitor"),
        "Visitation",
    ),
    Question(
        "If a riot occurs and DOC cannot control it, who should be notified?",
        "PD",
        ("PD", "Only visitors", "No one", "The inmates"),
        "Riot Procedures",
    ),
    Question(
        "For inmates participating in a riot, how much time is added once the situation is controlled?",
        "5 months",
        ("5 months", "1 month", "15 months", "No time can be added"),
        "Riot Procedures",
    ),
    Question(
        "What security class are regular inmates transported to prison by default?",
        "Low Security",
        ("Low Security", "High Security", "Maximum Security", "HUT"),
        "Inmates",
    ),
    Question(
        "What must happen once inmates arrive at prison?",
        "They must be fully searched",
        ("They must be fully searched", "They keep all weapons", "They choose their sentence", "They leave immediately"),
        "Inmates",
    ),
    Question(
        "How often, at minimum, can a random inmate search or area check happen?",
        "10+ minutes",
        ("10+ minutes", "Every 30 seconds", "Only once per shift", "Never"),
        "Inmates",
    ),
    Question(
        "How many times should an inmate's time be adjusted without Sergeant+ approval?",
        "No more than once",
        ("No more than once", "Unlimited times", "Exactly five times", "Only after release"),
        "Inmates",
    ),
    Question(
        "What classification do HUT inmates fall under by default?",
        "High Security",
        ("High Security", "Low Security", "No security", "Visitor status"),
        "HUT",
    ),
    Question(
        "Who has jurisdiction over HUT inmates?",
        "Department of Justice",
        ("Department of Justice", "Civilian visitors", "Any inmate", "Mechanics"),
        "HUT",
    ),
    Question(
        "What are Maximum Security inmates given?",
        "Food and water only",
        ("Food and water only", "Normal visitation and phone privileges", "Weapons", "Personal vehicles"),
        "Maximum Security",
    ),
    Question(
        "When must DOC make an incident report?",
        "Whenever an incident occurs while on duty",
        (
            "Whenever an incident occurs while on duty",
            "Only when off duty",
            "Only for vehicle purchases",
            "Never for misconduct",
        ),
        "Reports",
    ),
    Question(
        "For serious incidents involving HUT/Life Inmates or reporting another Corrections Officer, what should be used?",
        "The serious incident form",
        ("The serious incident form", "Only a radio message", "A vehicle repair command", "A visitor log"),
        "Reports",
    ),
    Question(
        "What should all weapons do while DOC is out in public?",
        "Remain holstered",
        ("Remain holstered", "Stay drawn", "Be handed to visitors", "Be left at City Hall"),
        "Conduct",
    ),
    Question(
        "At police stations, what should DOC mainly be there for?",
        "On-duty reasons such as transports",
        ("On-duty reasons such as transports", "Loitering", "Tasing other officers", "Personal arguments"),
        "Conduct",
    ),
    Question(
        "What is the minimum activity expectation every two weeks?",
        "Two hours",
        ("Two hours", "Ten hours", "Thirty minutes", "No activity required"),
        "Activity",
    ),
)


def code_questions() -> tuple[Question, ...]:
    answers = [answer for _, answer in CODE_ENTRIES]
    questions = []
    for index, (code, answer) in enumerate(CODE_ENTRIES):
        distractors = []
        offset = 1
        while len(distractors) < 3:
            candidate = answers[(index + offset) % len(answers)]
            if candidate != answer and candidate not in distractors:
                distractors.append(candidate)
            offset += 7
        questions.append(
            Question(
                f"What does {code} mean?",
                answer,
                tuple([answer, *distractors]),
                "10-Codes",
                written=index % 5 == 0,
                aliases=ANSWER_ALIASES.get(answer, ()),
                source_key=f"code:{code}",
            )
        )
    return tuple(questions)


QUIZ_BANK: tuple[Question, ...] = (
    *code_questions(),
    *SOP_QUESTIONS,
)


QUIZZES: tuple[Quiz, ...] = (
    Quiz(
        "corrections-officer",
        "Corrections Officer Exam",
        "Hard certification test for staff who really know the DOC SOP, 10-codes, command structure, reports, and conduct.",
        ("Radio", "Command", "Rules", "Divisions", "Equipment", "Weapons", "Vehicles", "Reports", "Conduct", "Activity"),
        include_codes=True,
        passing_score=94,
        difficulty="hard",
        question_count=40,
    ),
    Quiz(
        "solo-patrol",
        "Solo Patrol Exam",
        "Medium difficulty patrol test for practical decisions, transports, risk levels, force, radio traffic, and inmate control.",
        ("Radio", "Use of Force", "Risk Levels", "Weapons", "Vehicles", "Transport", "Riot Procedures", "Inmates", "Conduct", "Reports"),
        include_codes=True,
        passing_score=82,
        difficulty="medium",
        max_code_questions=18,
        question_count=25,
    ),
    Quiz(
        "ten-codes",
        "10-Codes Quiz",
        "Radio code practice for every DOC 10-code listed in the SOP.",
        (),
        include_codes=True,
    ),
    Quiz(
        "full-sop",
        "Full SOP Quiz",
        "The complete question bank covering every DOC SOP topic currently loaded.",
        None,
        include_codes=True,
    ),
)


def quiz_lookup() -> dict[str, Quiz]:
    return {quiz.slug: quiz for quiz in QUIZZES}


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back the transaction, then release the database handle."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def db_connection() -> sqlite3.Connection:
    db_path = Path(app.config["DATABASE"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    from mdt import callsign_number
    db.create_function("callsign_number", 1, callsign_number, deterministic=True)
    return db


def init_db() -> None:
    with db_connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_slug TEXT NOT NULL,
                quiz_title TEXT NOT NULL,
                trainee_name TEXT NOT NULL,
                callsign TEXT NOT NULL,
                score INTEGER NOT NULL,
                correct_count INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                passing_score INTEGER,
                passed INTEGER NOT NULL,
                responses_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS test_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_slug TEXT NOT NULL,
                quiz_title TEXT NOT NULL,
                trainee_name TEXT NOT NULL,
                callsign TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                started_at TEXT
            );

            CREATE TABLE IF NOT EXISTS custom_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_slug TEXT NOT NULL,
                prompt TEXT NOT NULL,
                answer TEXT NOT NULL,
                choices_json TEXT NOT NULL,
                category TEXT NOT NULL,
                written INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS question_overrides (
                question_key TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                answer TEXT NOT NULL,
                choices_json TEXT NOT NULL,
                category TEXT NOT NULL,
                written INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_username TEXT NOT NULL,
                name TEXT NOT NULL,
                contact_info TEXT NOT NULL,
                officer_reported TEXT NOT NULL,
                topic_datetime TEXT NOT NULL,
                event_details TEXT NOT NULL,
                evidence TEXT,
                witnesses TEXT,
                additional_info TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_type TEXT NOT NULL,
                discord_username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                callsign TEXT NOT NULL,
                rank TEXT NOT NULL,
                responses_json TEXT NOT NULL,
                decision_status TEXT NOT NULL DEFAULT 'pending',
                decided_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS application_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(application_id, user_id),
                FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS supervisor_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                callsign TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'supervisor',
                division_rank TEXT NOT NULL DEFAULT '',
                additional_division_rank TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cadets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                badge_number TEXT NOT NULL,
                discord TEXT,
                name TEXT NOT NULL,
                region TEXT,
                phase TEXT NOT NULL DEFAULT 'Onboarding',
                status TEXT NOT NULL DEFAULT 'Active',
                shifts INTEGER NOT NULL DEFAULT 0,
                hire_date TEXT,
                onboarding_interview INTEGER NOT NULL DEFAULT 0,
                onboarding_setup INTEGER NOT NULL DEFAULT 0,
                onboarding_academy INTEGER NOT NULL DEFAULT 0,
                phase1_step_1 INTEGER NOT NULL DEFAULT 0,
                phase1_step_2 INTEGER NOT NULL DEFAULT 0,
                phase1_step_3 INTEGER NOT NULL DEFAULT 0,
                phase1_step_4 INTEGER NOT NULL DEFAULT 0,
                phase1_deescalation_training INTEGER NOT NULL DEFAULT 0,
                phase1_tactical_training INTEGER NOT NULL DEFAULT 0,
                phase2_prison_transport_1 INTEGER NOT NULL DEFAULT 0,
                phase2_prison_transport_2 INTEGER NOT NULL DEFAULT 0,
                phase2_prison_transport_3 INTEGER NOT NULL DEFAULT 0,
                phase2_incident_report_1 INTEGER NOT NULL DEFAULT 0,
                phase2_incident_report_2 INTEGER NOT NULL DEFAULT 0,
                phase2_riot INTEGER NOT NULL DEFAULT 0,
                phase2_visitation INTEGER NOT NULL DEFAULT 0,
                solo_phase_start TEXT,
                solo_phase_end TEXT,
                final_exam_authorized INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cadet_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cadet_id INTEGER NOT NULL,
                timestamp_id INTEGER,
                training_officer TEXT NOT NULL,
                officer_badge TEXT NOT NULL,
                training_started TEXT NOT NULL,
                training_ended TEXT NOT NULL,
                training_timezone TEXT,
                activities TEXT NOT NULL,
                notes TEXT NOT NULL,
                logger_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (cadet_id) REFERENCES cadets(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                timestamp_id INTEGER,
                notification_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE,
                FOREIGN KEY (timestamp_id) REFERENCES cadet_timestamps(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_dismissals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                notification_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, notification_key),
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS application_review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS cadet_timestamps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cadet_user_id INTEGER NOT NULL,
                cadet_name TEXT NOT NULL DEFAULT '',
                cadet_callsign TEXT NOT NULL DEFAULT '',
                creator_name TEXT NOT NULL DEFAULT '',
                creator_callsign TEXT NOT NULL DEFAULT '',
                fto_name TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                start_timezone TEXT,
                stop_utc TEXT,
                stop_timezone TEXT,
                paused_utc TEXT,
                paused_total_seconds INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (cadet_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS timestamp_ftos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fto_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cadet_user_id INTEGER NOT NULL,
                cadet_name TEXT NOT NULL,
                cadet_callsign TEXT NOT NULL,
                fto_name TEXT NOT NULL,
                teaching_rating INTEGER NOT NULL,
                helpful_rating INTEGER NOT NULL,
                overall_rating INTEGER,
                feedback TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (cadet_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS training_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trainee_name TEXT NOT NULL,
                callsign TEXT NOT NULL,
                category_slug TEXT NOT NULL,
                category_title TEXT NOT NULL,
                correct_count INTEGER NOT NULL,
                multiple_choice_total INTEGER NOT NULL,
                answered_count INTEGER NOT NULL,
                skipped_count INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                responses_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS division_training_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_user_id INTEGER NOT NULL,
                trainer_name TEXT NOT NULL,
                trainer_callsign TEXT NOT NULL,
                training_title TEXT NOT NULL,
                departments_json TEXT NOT NULL,
                training_datetime TEXT NOT NULL,
                attendees TEXT,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (trainer_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS division_certification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                certified_user_id INTEGER NOT NULL,
                certified_name TEXT NOT NULL,
                certified_callsign TEXT NOT NULL,
                certifier_user_id INTEGER NOT NULL,
                certifier_name TEXT NOT NULL,
                certifier_callsign TEXT NOT NULL,
                certification_name TEXT NOT NULL,
                certification_datetime TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (certified_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE,
                FOREIGN KEY (certifier_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS dashboard_announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_user_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                author_callsign TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL,
                FOREIGN KEY (author_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS dashboard_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_user_id INTEGER NOT NULL,
                creator_name TEXT NOT NULL,
                creator_callsign TEXT NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT,
                event_start_utc TEXT,
                event_timezone TEXT,
                event_type TEXT NOT NULL DEFAULT 'Training',
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (creator_user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                callsign TEXT NOT NULL,
                role TEXT NOT NULL,
                division_rank TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES supervisor_users(id) ON DELETE CASCADE
            );

            UPDATE dashboard_events
            SET event_type = 'General'
            WHERE event_type NOT IN ('General', 'Training');
            """
        )
        existing_columns = {row["name"] for row in db.execute("PRAGMA table_info(cadets)").fetchall()}
        for column in CADET_PROGRESS_CHECKS:
            if column not in existing_columns:
                db.execute(f"ALTER TABLE cadets ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        for column in CADET_PROGRESS_TEXT:
            if column not in existing_columns:
                db.execute(f"ALTER TABLE cadets ADD COLUMN {column} TEXT")
        log_columns = {row["name"] for row in db.execute("PRAGMA table_info(cadet_logs)").fetchall()}
        if "training_timezone" not in log_columns:
            db.execute("ALTER TABLE cadet_logs ADD COLUMN training_timezone TEXT")
        if "timestamp_id" not in log_columns:
            db.execute("ALTER TABLE cadet_logs ADD COLUMN timestamp_id INTEGER")
        application_columns = {row["name"] for row in db.execute("PRAGMA table_info(applications)").fetchall()}
        if "decision_status" not in application_columns:
            db.execute("ALTER TABLE applications ADD COLUMN decision_status TEXT NOT NULL DEFAULT 'pending'")
        if "decided_at" not in application_columns:
            db.execute("ALTER TABLE applications ADD COLUMN decided_at TEXT")
        timestamp_columns = {row["name"] for row in db.execute("PRAGMA table_info(cadet_timestamps)").fetchall()}
        if "cadet_name" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN cadet_name TEXT NOT NULL DEFAULT ''")
        if "cadet_callsign" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN cadet_callsign TEXT NOT NULL DEFAULT ''")
        if "creator_name" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN creator_name TEXT NOT NULL DEFAULT ''")
        if "creator_callsign" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN creator_callsign TEXT NOT NULL DEFAULT ''")
        if "paused_utc" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN paused_utc TEXT")
        if "paused_total_seconds" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN paused_total_seconds INTEGER NOT NULL DEFAULT 0")
        if "archived_at" not in timestamp_columns:
            db.execute("ALTER TABLE cadet_timestamps ADD COLUMN archived_at TEXT")
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(supervisor_users)").fetchall()}
        if "display_name" not in user_columns:
            db.execute("ALTER TABLE supervisor_users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        if "callsign" not in user_columns:
            db.execute("ALTER TABLE supervisor_users ADD COLUMN callsign TEXT NOT NULL DEFAULT ''")
        if "phone_number" not in user_columns:
            db.execute("ALTER TABLE supervisor_users ADD COLUMN phone_number TEXT NOT NULL DEFAULT ''")
        if "division_rank" not in user_columns:
            try:
                db.execute("ALTER TABLE supervisor_users ADD COLUMN division_rank TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as error:
                if "duplicate column name" not in str(error).lower():
                    raise
        if "additional_division_rank" not in user_columns:
            db.execute("ALTER TABLE supervisor_users ADD COLUMN additional_division_rank TEXT NOT NULL DEFAULT ''")
        db.executemany(
            "UPDATE supervisor_users SET division_rank = ? WHERE division_rank = ?",
            (
                ("ert_training_officer", "training_officer"),
                ("ftp_senior_trainer", "senior_trainer"),
                ("ert_supervisor", "division_supervisor"),
                ("ert_command", "division_commander"),
            ),
        )
        # The former standalone FTO account role is now a normal FTP division membership.
        # Preserve existing access while keeping panel access entirely division-based.
        db.execute(
            "UPDATE supervisor_users SET role = 'officer', division_rank = 'ftp_member' WHERE role = 'fto' AND division_rank = ''"
        )
        db.execute("UPDATE supervisor_users SET role = 'officer' WHERE role = 'fto'")
        event_columns = {row["name"] for row in db.execute("PRAGMA table_info(dashboard_events)").fetchall()}
        if "event_start_utc" not in event_columns:
            db.execute("ALTER TABLE dashboard_events ADD COLUMN event_start_utc TEXT")
        if "event_timezone" not in event_columns:
            db.execute("ALTER TABLE dashboard_events ADD COLUMN event_timezone TEXT")
        source_timezone = ZoneInfo(app.config["APP_TIMEZONE"])
        for event in db.execute("SELECT id, event_date, event_time FROM dashboard_events WHERE event_start_utc IS NULL AND COALESCE(event_time, '') != ''").fetchall():
            try:
                local_start = datetime.strptime(f"{event['event_date']} {event['event_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=source_timezone)
            except ValueError:
                continue
            db.execute(
                "UPDATE dashboard_events SET event_start_utc = ?, event_timezone = ? WHERE id = ?",
                (local_start.astimezone(ZoneInfo("UTC")).isoformat(), app.config["APP_TIMEZONE"], event["id"]),
            )
        fto_count = db.execute("SELECT COUNT(*) FROM timestamp_ftos").fetchone()[0]
        if fto_count == 0:
            db.executemany(
                "INSERT OR IGNORE INTO timestamp_ftos (name, created_at) VALUES (?, ?)",
                [(name, now_text()) for name in DEFAULT_FTO_OPTIONS],
            )
        cadets_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cadets'"
        ).fetchone()
        if cadets_sql and "badge_number TEXT NOT NULL UNIQUE" in cadets_sql["sql"]:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("DROP TABLE IF EXISTS cadets_unique_backup")
            db.execute("DROP TABLE IF EXISTS cadet_logs_backup")
            db.execute("ALTER TABLE cadets RENAME TO cadets_unique_backup")
            db.execute("ALTER TABLE cadet_logs RENAME TO cadet_logs_backup")
            db.executescript(
                """
                CREATE TABLE cadets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    badge_number TEXT NOT NULL,
                    discord TEXT,
                    name TEXT NOT NULL,
                    region TEXT,
                    phase TEXT NOT NULL DEFAULT 'Onboarding',
                    status TEXT NOT NULL DEFAULT 'Active',
                    shifts INTEGER NOT NULL DEFAULT 0,
                    hire_date TEXT,
                    onboarding_interview INTEGER NOT NULL DEFAULT 0,
                    onboarding_setup INTEGER NOT NULL DEFAULT 0,
                    onboarding_academy INTEGER NOT NULL DEFAULT 0,
                    phase1_step_1 INTEGER NOT NULL DEFAULT 0,
                    phase1_step_2 INTEGER NOT NULL DEFAULT 0,
                    phase1_step_3 INTEGER NOT NULL DEFAULT 0,
                    phase1_step_4 INTEGER NOT NULL DEFAULT 0,
                    phase1_deescalation_training INTEGER NOT NULL DEFAULT 0,
                    phase1_tactical_training INTEGER NOT NULL DEFAULT 0,
                    phase2_prison_transport_1 INTEGER NOT NULL DEFAULT 0,
                    phase2_prison_transport_2 INTEGER NOT NULL DEFAULT 0,
                    phase2_prison_transport_3 INTEGER NOT NULL DEFAULT 0,
                    phase2_incident_report_1 INTEGER NOT NULL DEFAULT 0,
                    phase2_incident_report_2 INTEGER NOT NULL DEFAULT 0,
                    phase2_riot INTEGER NOT NULL DEFAULT 0,
                    phase2_visitation INTEGER NOT NULL DEFAULT 0,
                    solo_phase_start TEXT,
                    solo_phase_end TEXT,
                    final_exam_authorized INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                INSERT INTO cadets (
                    id, badge_number, discord, name, region, phase, status, shifts, hire_date,
                    onboarding_interview, onboarding_setup, onboarding_academy,
                    phase1_step_1, phase1_step_2, phase1_step_3, phase1_step_4,
                    phase1_deescalation_training, phase1_tactical_training,
                    phase2_prison_transport_1, phase2_prison_transport_2, phase2_prison_transport_3,
                    phase2_incident_report_1, phase2_incident_report_2, phase2_riot, phase2_visitation,
                    solo_phase_start, solo_phase_end, final_exam_authorized, created_at
                )
                SELECT
                    id, badge_number, discord, name, region, phase, status, shifts, hire_date,
                    onboarding_interview, onboarding_setup, onboarding_academy,
                    phase1_step_1, phase1_step_2, phase1_step_3, phase1_step_4,
                    0, 0,
                    phase2_prison_transport_1, phase2_prison_transport_2, phase2_prison_transport_3,
                    phase2_incident_report_1, phase2_incident_report_2, phase2_riot, phase2_visitation,
                    solo_phase_start, solo_phase_end, final_exam_authorized, created_at
                FROM cadets_unique_backup;

                CREATE TABLE cadet_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cadet_id INTEGER NOT NULL,
                    training_officer TEXT NOT NULL,
                    officer_badge TEXT NOT NULL,
                    training_started TEXT NOT NULL,
                    training_ended TEXT NOT NULL,
                    training_timezone TEXT,
                    activities TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    logger_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (cadet_id) REFERENCES cadets(id) ON DELETE CASCADE
                );
                """
            )
            backup_log_columns = {row["name"] for row in db.execute("PRAGMA table_info(cadet_logs_backup)").fetchall()}
            if "training_timezone" in backup_log_columns:
                db.execute(
                    """
                    INSERT INTO cadet_logs (
                        id, cadet_id, training_officer, officer_badge, training_started,
                        training_ended, training_timezone, activities, notes, logger_status, created_at
                    )
                    SELECT
                        id, cadet_id, training_officer, officer_badge, training_started,
                        training_ended, training_timezone, activities, notes, logger_status, created_at
                    FROM cadet_logs_backup
                    """
                )
            else:
                db.execute(
                    """
                    INSERT INTO cadet_logs (
                        id, cadet_id, training_officer, officer_badge, training_started,
                        training_ended, training_timezone, activities, notes, logger_status, created_at
                    )
                    SELECT
                        id, cadet_id, training_officer, officer_badge, training_started,
                        training_ended, NULL, activities, notes, logger_status, created_at
                    FROM cadet_logs_backup
                    """
                )
            db.execute("DROP TABLE cadets_unique_backup")
            db.execute("DROP TABLE cadet_logs_backup")
            db.execute("PRAGMA foreign_keys = ON")
        old_owner = db.execute(
            "SELECT * FROM supervisor_users WHERE lower(username) = 'owner' AND role = 'owner'"
        ).fetchone()
        desired_owner = app.config["OWNER_USERNAME"].strip()
        if old_owner and desired_owner.lower() != "owner":
            existing_desired = db.execute(
                "SELECT * FROM supervisor_users WHERE lower(username) = lower(?)",
                (desired_owner,),
            ).fetchone()
            if existing_desired and existing_desired["id"] != old_owner["id"]:
                db.execute(
                    "UPDATE supervisor_users SET password_hash = ?, role = 'owner' WHERE id = ?",
                    (old_owner["password_hash"], existing_desired["id"]),
                )
                db.execute("DELETE FROM supervisor_users WHERE id = ?", (old_owner["id"],))
            else:
                db.execute(
                    "UPDATE supervisor_users SET username = ?, role = 'owner' WHERE id = ?",
                    (desired_owner, old_owner["id"]),
                )
        user_count = db.execute("SELECT COUNT(*) FROM supervisor_users").fetchone()[0]
        if user_count == 0:
            db.execute(
                """
                INSERT INTO supervisor_users (username, password_hash, role, created_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (
                    app.config["OWNER_USERNAME"],
                    generate_password_hash(app.config["SUPERVISOR_PASSWORD"]),
                    now_text(),
                ),
            )


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_now_text(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def test_request_expires_at(request_row: sqlite3.Row) -> datetime | None:
    created_at = parse_now_text(request_row["created_at"])
    if created_at is None:
        return None
    return created_at + timedelta(minutes=TEST_REQUEST_EXPIRY_MINUTES)


def test_request_seconds_remaining(request_row: sqlite3.Row) -> int:
    if request_row["status"] != "pending":
        return 0
    expires_at = test_request_expires_at(request_row)
    if expires_at is None:
        return 0
    return max(0, int((expires_at - datetime.now()).total_seconds()))


def expire_stale_test_requests() -> None:
    cutoff = (datetime.now() - timedelta(minutes=TEST_REQUEST_EXPIRY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    with db_connection() as db:
        db.execute(
            """
            UPDATE test_requests
            SET status = 'expired', reviewed_at = COALESCE(reviewed_at, ?), reviewed_by = COALESCE(reviewed_by, 'System')
            WHERE status = 'pending' AND created_at <= ?
            """,
            (now_text(), cutoff),
        )


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "T" not in normalized and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return datetime.utcfromtimestamp(parsed.timestamp())
    return parsed


def paused_seconds_between(paused_utc: str | None, end_utc: str | None) -> int:
    paused_at = parse_timestamp(paused_utc)
    ended_at = parse_timestamp(end_utc)
    if paused_at is None or ended_at is None:
        return 0
    return max(0, int((ended_at - paused_at).total_seconds()))


def rows_all(query: str, args: tuple = ()) -> list[sqlite3.Row]:
    with db_connection() as db:
        return list(db.execute(query, args).fetchall())


def row_one(query: str, args: tuple = ()) -> sqlite3.Row | None:
    with db_connection() as db:
        return db.execute(query, args).fetchone()


def current_user() -> sqlite3.Row | None:
    user_id = session.get("supervisor_user_id")
    if not user_id:
        return None
    user = row_one("SELECT id, username, display_name, callsign, phone_number, role, division_rank, additional_division_rank, created_at, auth_version FROM supervisor_users WHERE id = ?", (user_id,))
    if user and session.get('auth_version', 0) != user['auth_version']:
        session.clear()
        return None
    return user


def user_division_ranks(user: sqlite3.Row | dict | None = None) -> set[str]:
    if user is None:
        user = current_user()
    if user is None:
        return set()
    if isinstance(user, dict):
        values = (user.get("division_rank", ""), user.get("additional_division_rank", ""))
    else:
        keys = set(user.keys())
        values = (user["division_rank"], user["additional_division_rank"] if "additional_division_rank" in keys else "")
    return {str(value).strip() for value in values if str(value).strip()}


def user_has_division_rank(user: sqlite3.Row | dict | None, ranks: tuple[str, ...] | set[str]) -> bool:
    return bool(user_division_ranks(user) & set(ranks))


def user_division_rank_in(user: sqlite3.Row | dict | None, ranks: tuple[str, ...]) -> str:
    assigned = user_division_ranks(user)
    return next((rank for rank in ranks if rank in assigned), "")


def normalized_division_assignments(ert_rank: str, ftp_rank: str) -> tuple[str, str] | None:
    ert_rank = ert_rank.strip()
    ftp_rank = ftp_rank.strip()
    if ert_rank not in ("", *ERT_DIVISION_RANKS) or ftp_rank not in ("", *FTP_DIVISION_RANKS):
        return None
    return (ert_rank or ftp_rank, ftp_rank if ert_rank else "")


def account_identity_values(user: sqlite3.Row | None = None) -> dict[str, str]:
    user = user or current_user()
    if user is None:
        return {}
    account_name = (user["display_name"] or user["username"]).strip()
    callsign = (user["callsign"] or "").strip()
    return {
        "full_name": account_name,
        "trainee_name": account_name,
        "training_officer": account_name,
        "callsign": callsign,
        "officer_badge": callsign,
    }


def sheet_roster_members(sheet_url: str, cache: dict[str, object], default_role: str) -> tuple[list[dict[str, str]], bool]:
    """Read active positions from a shared Google Sheet, with a short in-memory cache."""
    cache_age = time.monotonic() - float(cache["fetched_at"])
    if cache_age < app.config["ERT_ROSTER_CACHE_SECONDS"]:
        return list(cache["members"]), bool(cache["available"])

    try:
        with urlopen(sheet_url, timeout=5) as response:
            rows = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8-sig", newline=""))
            members = []
            for row in rows:
                name = (row.get("Name") or "").strip()
                status = (row.get("Status") or "").strip()
                badge = (row.get("Overwatch Badge No.") or row.get("Badge No.") or "").strip()
                role = (row.get("Role") or "").strip()
                if (
                    not name
                    or name.casefold() in {"name", "vacant"}
                    or status.casefold() in {"status", "vacant"}
                    or badge in {"/", "Badge No.", "Overwatch Badge No."}
                ):
                    continue
                members.append(
                    {
                        "name": name,
                        "badge": badge,
                        "role": role or default_role,
                        "status": status or "Active",
                        "timezone": (row.get("Time Zone") or "").strip(),
                        "joined": (row.get("Join Date") or "").strip(),
                    }
                )
    except (OSError, URLError, ValueError, csv.Error):
        members = []
    else:
        previous_members = list(cache.get("members", []))
        member_key = lambda member: (member.get("badge") or member.get("name", "")).casefold()
        previous_by_key = {member_key(member): member for member in previous_members}
        changed_keys = {
            member_key(member)
            for member in members
            if previous_members and (member_key(member) not in previous_by_key or previous_by_key[member_key(member)] != member)
        }
        cache.update(
            fetched_at=time.monotonic(),
            synced_at=time.time(),
            members=members,
            changed_keys=changed_keys,
            available=True,
        )
        return members, True

    # A temporary sheet outage should not make the panel unusable. Retry shortly and use account assignments meanwhile.
    cache.update(fetched_at=time.monotonic(), members=[], available=False)
    return [], False


def ert_roster_members() -> tuple[list[dict[str, str]], bool]:
    return sheet_roster_members(app.config["ERT_ROSTER_SHEET_URL"], _ERT_ROSTER_CACHE, "ERT Personnel")


def ftp_roster_members() -> tuple[list[dict[str, str]], bool]:
    return sheet_roster_members(app.config["FTP_ROSTER_SHEET_URL"], _FTP_ROSTER_CACHE, "FTP Personnel")


def official_doc_roster_members(force_refresh: bool = False) -> tuple[list[dict[str, str]], bool]:
    """Read the main Official DOC Roster sheet, excluding End of Watch and vacant records."""
    cache_age = time.monotonic() - float(_DOC_ROSTER_CACHE["fetched_at"])
    if not force_refresh and cache_age < app.config["ERT_ROSTER_CACHE_SECONDS"]:
        return list(_DOC_ROSTER_CACHE["members"]), bool(_DOC_ROSTER_CACHE["available"])
    allowed_statuses = {"active", "semi-active", "inactive", "loa"}
    try:
        with urlopen(app.config["DOC_ROSTER_SHEET_URL"], timeout=5) as response:
            sheet_rows = list(csv.reader(io.TextIOWrapper(response, encoding="utf-8-sig", newline="")))
        header_index = next(
            index for index, row in enumerate(sheet_rows)
            if any(cell.strip().casefold() == "call sign" for cell in row)
        )
        headers = [cell.strip() for cell in sheet_rows[header_index]]
        members = []
        section_index = 0
        for values in sheet_rows[header_index + 1:]:
            row = {headers[index]: values[index].strip() if index < len(values) else "" for index in range(len(headers))}
            callsign = row.get("Call Sign", "")
            name = row.get("Name", "")
            status = row.get("Status", "")
            if not callsign and not name:
                if members:
                    section_index += 1
                continue
            if not callsign or not name or name.casefold() == "vacant" or status.casefold() not in allowed_statuses:
                continue
            members.append({
                "callsign": callsign,
                "name": name,
                "rank": row.get("Rank", "") or "DOC Personnel",
                "authority": row.get("Authority", "") or "Department Personnel",
                "status": status,
                "timezone": row.get("Timezone", ""),
                "last_promoted": row.get("Last Promotion", ""),
                "joined": row.get("Joining DOC", ""),
                "loa_end": row.get("LOA End", ""),
                "ftp_assignment": row.get("FTP", ""),
                "ert_assignment": row.get("ERT", ""),
                "certification_values": {
                    column: row.get(column, "")
                    for column, _ in PROFILE_CERTIFICATION_COLUMNS
                },
                "section": str(section_index),
            })
    except (OSError, URLError, ValueError, csv.Error, StopIteration):
        _DOC_ROSTER_CACHE.update(fetched_at=time.monotonic(), members=[], available=False)
        return [], False
    previous_members = list(_DOC_ROSTER_CACHE.get("members", []))
    member_key = lambda member: (member.get("callsign") or member.get("name", "")).casefold()
    previous_by_key = {member_key(member): member for member in previous_members}
    _DOC_ROSTER_CACHE.update(
        fetched_at=time.monotonic(),
        synced_at=time.time(),
        members=members,
        changed_keys={
            member_key(member)
            for member in members
            if previous_members and (member_key(member) not in previous_by_key or previous_by_key[member_key(member)] != member)
        },
        available=True,
    )
    return members, True


def roster_sync_metadata(cache: dict[str, object]) -> dict[str, object]:
    return {
        "synced_at": int(float(cache.get("synced_at", 0.0))),
        # Keep this payload JSON-safe because the manual roster refresh endpoint
        # returns it directly. Templates can perform membership checks on a list.
        "changed_keys": sorted(str(key) for key in cache.get("changed_keys", set())),
    }


def normalized_roster_identity(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def roster_member_for_account(user: sqlite3.Row | dict, members: list[dict[str, str]]) -> dict[str, str] | None:
    callsign = normalized_roster_identity(user["callsign"])
    if callsign:
        match = next((member for member in members if normalized_roster_identity(member.get("callsign")) == callsign), None)
        if match:
            return match
    name = normalized_roster_identity(user["display_name"] or user["username"])
    return next((member for member in members if normalized_roster_identity(member.get("name")) == name), None)


def account_for_roster_member(member: dict[str, str]) -> sqlite3.Row | None:
    member_callsign = normalized_roster_identity(member.get("callsign"))
    member_name = normalized_roster_identity(member.get("name"))
    for account in supervisor_users():
        if member_callsign and normalized_roster_identity(account["callsign"]) == member_callsign:
            return account
    return next(
        (
            account
            for account in supervisor_users()
            if member_name and normalized_roster_identity(account["display_name"] or account["username"]) == member_name
        ),
        None,
    )


def profile_certifications(member: dict[str, object] | None) -> list[dict[str, str]]:
    values = member.get("certification_values", {}) if member else {}
    certifications = []
    for column, label in PROFILE_CERTIFICATION_COLUMNS:
        raw_value = str(values.get(column, "") if isinstance(values, dict) else "").strip()
        normalized = raw_value.casefold()
        if raw_value in {"✔", "✓"} or normalized in {"yes", "true", "certified", "1"}:
            status, status_class = "Certified", "certified"
        elif raw_value == "!" or normalized in {"review", "pending"}:
            status, status_class = "Review required", "review"
        else:
            status, status_class = "Not certified", "not-certified"
        certifications.append({"key": column.lower(), "label": label, "status": status, "status_class": status_class})
    return certifications


def profile_division_badges(display_name: str) -> list[dict[str, str]]:
    # Division badges have their own numbering; match exact normalized character names,
    # never an admin role or a coincidentally matching division badge number.
    identity = normalized_roster_identity(display_name)
    badges = []
    for division, loader, image in [('ERT', ert_roster_members, 'profile-ert.png'), ('FTP', ftp_roster_members, 'profile-ftp.png')]:
        members, available = loader()
        if identity and available and any(normalized_roster_identity(row.get('name')) == identity for row in members):
            badges.append({'name': division, 'image': image})
    return badges


def employee_profile_context(account: sqlite3.Row | None, member: dict[str, object] | None, roster_available: bool) -> dict[str, object]:
    display_name = str((member or {}).get("name") or ((account["display_name"] or account["username"]) if account else "DOC Employee"))
    callsign = str((member or {}).get("callsign") or (account["callsign"] if account else ""))
    division_labels = [DIVISION_RANK_LABELS.get(rank, rank) for rank in user_division_ranks(account)] if account else []
    ftp_assignment = str((member or {}).get("ftp_assignment") or "").strip()
    ert_assignment = str((member or {}).get("ert_assignment") or "").strip()
    if ftp_assignment in {"", "-"}:
        ftp_assignment = next((label for label in division_labels if label.startswith("FTP")), "Not assigned")
    else:
        ftp_assignment = f"FTP · {ftp_assignment}"
    if ert_assignment in {"", "-"}:
        ert_assignment = next((label for label in division_labels if label.startswith("ERT")), "Not assigned")
    else:
        ert_assignment = f"ERT · {ert_assignment}"
    certifications = profile_certifications(member)
    return {
        "account": account,
        "member": member,
        "display_name": display_name,
        "division_badges": profile_division_badges(display_name),
        "callsign": callsign,
        "rank": str((member or {}).get("rank") or ((account["role"].replace("_", " ").title()) if account else "DOC Personnel")),
        "status": str((member or {}).get("status") or "Active"),
        "timezone": str((member or {}).get("timezone") or "Not listed"),
        "authority": str((member or {}).get("authority") or "Department Personnel"),
        "joined": str((member or {}).get("joined") or (account["created_at"][:10] if account and account["created_at"] else "Not listed")),
        "last_promoted": str((member or {}).get("last_promoted") or "Not listed"),
        "ftp_assignment": ftp_assignment,
        "ert_assignment": ert_assignment,
        "certifications": certifications,
        "roster_available": roster_available,
        "roster_linked": member is not None,
        "roster_sync": roster_sync_metadata(_DOC_ROSTER_CACHE),
    }


def user_can_manage_questions(user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(user and user["role"] in ADMIN_ROLES)


def user_can_view_division_applications(user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(user and (user["role"] in ADMIN_ROLES or user_has_division_rank(user, DIVISION_APPLICATION_RANKS)))


def user_can_review_division_applications(application_type: str, user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(
        user
        and application_type in DIVISION_APPLICATION_REVIEW_RANKS
        and (user["role"] in ADMIN_ROLES or user_has_division_rank(user, DIVISION_APPLICATION_REVIEW_RANKS[application_type]))
    )


def user_can_override_division_application(application_type: str, user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(user and application_type in {"ftp", "ert"} and (
        user["role"] in ADMIN_ROLES
        or user_has_division_rank(user, {f"{application_type}_command"})
    ))


def division_application_redirect(application_type: str) -> str:
    if application_type == "ert":
        return redirect(url_for("ert_panel", _anchor="ert-applications-tab"))
    return redirect(url_for("fto_panel", _anchor="ftp-applications-tab"))


def user_can_access_ftp(user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(user and (user["role"] in FTO_ROLES or user_has_division_rank(user, FTP_DIVISION_RANKS)))


def user_can_access_ert(user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(user and (user["role"] in ADMIN_ROLES or user_has_division_rank(user, ERT_DIVISION_RANKS)))


def user_can_manage_dashboard(user: sqlite3.Row | None = None) -> bool:
    user = user or current_user()
    return bool(
        user
        and (
            user["role"] in ADMIN_ROLES
            or user_has_division_rank(user, (*ERT_DIVISION_RANKS[1:], *FTP_DIVISION_RANKS[1:]))
        )
    )


def supervisor_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if user["role"] not in SUPERVISOR_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("supervisor_login"))
        return view(*args, **kwargs)

    return wrapped


def fto_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if not user_can_access_ftp(user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def cadet_panel_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if user["role"] not in CADET_PANEL_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def employee_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if user["role"] not in EMPLOYEE_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if user["role"] not in ADMIN_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if user["role"] != "owner":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def division_applications_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if not user_can_view_division_applications(user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def ert_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("supervisor_login"))
        if not user_can_access_ert(user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def questions_for_quiz(quiz: Quiz) -> tuple[Question, ...]:
    base_bank = base_questions_for_quiz(quiz)
    return tuple([*apply_question_overrides(base_bank), *custom_questions_for_quiz(quiz.slug)])


def base_questions_for_quiz(quiz: Quiz) -> tuple[Question, ...]:
    code_bank = code_questions() if quiz.include_codes else ()
    if quiz.max_code_questions is not None:
        code_bank = code_bank[: quiz.max_code_questions]
    if quiz.categories is None:
        return tuple([*code_bank, *keyed_sop_questions()])
    else:
        sop_bank = tuple(question for question in keyed_sop_questions() if question.category in quiz.categories)
        return tuple([*code_bank, *sop_bank])


def stable_question_key(question: Question) -> str:
    base = f"{question.category}|{question.prompt}|{question.answer}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def keyed_question(question: Question, source_key: str) -> Question:
    return Question(
        question.prompt,
        question.answer,
        question.choices,
        question.category,
        written=question.written,
        aliases=question.aliases,
        source_key=source_key,
    )


def keyed_sop_questions() -> tuple[Question, ...]:
    return tuple(keyed_question(question, f"sop:{index}") for index, question in enumerate(SOP_QUESTIONS))


def question_overrides() -> dict[str, sqlite3.Row]:
    return {row["question_key"]: row for row in rows_all("SELECT * FROM question_overrides")}


def apply_question_overrides(questions: tuple[Question, ...]) -> tuple[Question, ...]:
    overrides = question_overrides()
    prepared = []
    for question in questions:
        key = question.source_key or stable_question_key(question)
        override = overrides.get(key)
        if override is None:
            prepared.append(question)
            continue
        prepared.append(
            Question(
                override["prompt"],
                override["answer"],
                tuple(json.loads(override["choices_json"])),
                override["category"],
                written=bool(override["written"]),
                aliases=question.aliases,
                source_key=key,
            )
        )
    return tuple(prepared)


def custom_questions_for_quiz(slug: str) -> tuple[Question, ...]:
    rows = rows_all("SELECT * FROM custom_questions WHERE quiz_slug = ? ORDER BY id", (slug,))
    questions = []
    for row in rows:
        choices = tuple(json.loads(row["choices_json"]))
        questions.append(
            Question(
                row["prompt"],
                row["answer"],
                choices,
                row["category"],
                written=bool(row["written"]),
            )
        )
    return tuple(questions)


def quiz_question_total(quiz: Quiz, questions: tuple[Question, ...]) -> int:
    if quiz.question_count is None:
        return len(questions)
    return min(quiz.question_count, len(questions))


def sample_questions_for_counts(questions: tuple[Question, ...], quiz: Quiz) -> tuple[Question, ...]:
    return questions[: quiz_question_total(quiz, questions)]


def mark_written_questions(questions: tuple[Question, ...], difficulty: str = "standard") -> tuple[Question, ...]:
    prepared = []
    for index, question in enumerate(questions):
        if difficulty == "hard":
            should_be_written = (question.category == "10-Codes" and index % 4 == 0) or (
                question.category != "10-Codes" and index % 3 == 1
            )
        elif difficulty == "medium":
            should_be_written = question.written or (question.category != "10-Codes" and index % 7 == 1)
        else:
            should_be_written = question.written or (question.category != "10-Codes" and index % 4 == 1)

        if should_be_written:
            prepared.append(
                Question(
                    question.prompt,
                    question.answer,
                    question.choices,
                    question.category,
                    written=True,
                    aliases=question.aliases,
                    source_key=question.source_key,
                )
            )
        else:
            prepared.append(question)
    return tuple(prepared)


def quiz_cards(slugs: set[str] | None = None) -> list[dict[str, object]]:
    cards = []
    for quiz in QUIZZES:
        if slugs is not None and quiz.slug not in slugs:
            continue
        questions = mark_written_questions(questions_for_quiz(quiz), quiz.difficulty)
        cards.append(
            {
                "slug": quiz.slug,
                "title": quiz.title,
                "summary": quiz.summary,
                "count": quiz_question_total(quiz, questions),
                "passing_score": quiz.passing_score,
                "difficulty": quiz.difficulty.title(),
                "href": url_for("quiz", slug=quiz.slug),
            }
        )
    return cards


def supervisor_quiz_rows() -> list[dict[str, object]]:
    rows = []
    for quiz in QUIZZES:
        if quiz.slug not in CERT_QUIZZES:
            continue
        questions = mark_written_questions(questions_for_quiz(quiz), quiz.difficulty)
        sample_questions = sample_questions_for_counts(questions, quiz)
        written_count = sum(1 for question in sample_questions if question.written)
        rows.append(
            {
                "title": quiz.title,
                "difficulty": quiz.difficulty.title(),
                "passing_score": quiz.passing_score,
                "question_count": len(sample_questions),
                "written_count": written_count,
                "multiple_choice_count": len(sample_questions) - written_count,
            }
        )
    return rows


def supervisor_submissions() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT id, quiz_title, trainee_name, callsign, score, correct_count, total_count,
               passing_score, passed, created_at
        FROM submissions
        WHERE quiz_slug IN ('corrections-officer', 'solo-patrol')
        ORDER BY id DESC
        LIMIT 30
        """
    )


def supervisor_training_quiz_results() -> list[dict[str, object]]:
    results = []
    for row in rows_all(
        """
        SELECT id, quiz_slug, quiz_title, trainee_name, callsign, score, correct_count,
               total_count, responses_json, created_at
        FROM submissions
        WHERE quiz_slug IN ('ten-codes', 'full-sop')
        ORDER BY id DESC
        LIMIT 50
        """
    ):
        result = dict(row)
        try:
            result["responses"] = json.loads(row["responses_json"])
        except (TypeError, json.JSONDecodeError):
            result["responses"] = []
        result["answered_count"] = sum(1 for item in result["responses"] if item.get("selected"))
        result["skipped_count"] = len(result["responses"]) - int(result["answered_count"])
        results.append(result)
    return results


def supervisor_test_requests() -> list[sqlite3.Row]:
    expire_stale_test_requests()
    return rows_all(
        """
        SELECT id, quiz_slug, quiz_title, trainee_name, callsign, status, created_at,
               reviewed_at, reviewed_by, started_at
        FROM test_requests
        WHERE status = 'pending'
        ORDER BY id DESC
        LIMIT 40
        """
    )


def supervisor_custom_questions() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT custom_questions.*, custom_questions.quiz_slug AS slug
        FROM custom_questions
        ORDER BY id DESC
        """
    )


def supervisor_complaints() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM complaints
        ORDER BY id DESC
        LIMIT 50
        """
    )


def division_applications(application_type: str) -> list[dict[str, object]]:
    if application_type not in DIVISION_APPLICATION_REVIEW_RANKS:
        return []
    rows = rows_all(
        "SELECT * FROM applications WHERE application_type = ? ORDER BY id DESC LIMIT 80",
        (application_type,),
    )
    user = current_user()
    user_votes = application_votes_for_user(user["id"]) if user else {}
    required_votes = application_voter_count(application_type)
    applications = []
    for row in rows:
        application = dict(row)
        application["responses"] = json.loads(row["responses_json"])
        application.update(application_vote_summary(int(row["id"]), application_type))
        application["user_vote"] = user_votes.get(int(row["id"]), "")
        application["required_votes"] = required_votes
        application["is_final"] = application["decision_status"] in {"accepted", "denied"}
        application["can_command_override"] = user_can_override_division_application(application_type, user)
        application["review_history"] = application_review_history(int(row["id"]))
        applications.append(application)
    return applications


def supervisor_applications() -> list[dict[str, object]]:
    return [*division_applications("ert"), *division_applications("ftp")]


def application_votes_for_user(user_id: int) -> dict[int, str]:
    rows = rows_all(
        """
        SELECT application_id, vote
        FROM application_votes
        WHERE user_id = ?
        """,
        (user_id,),
    )
    return {int(row["application_id"]): row["vote"] for row in rows}


def application_review_history(application_id: int) -> list[dict[str, str]]:
    rows = rows_all(
        """
        SELECT application_review_events.*, supervisor_users.display_name, supervisor_users.username, supervisor_users.callsign
        FROM application_review_events
        LEFT JOIN supervisor_users ON supervisor_users.id = application_review_events.user_id
        WHERE application_review_events.application_id = ?
        ORDER BY application_review_events.id DESC
        LIMIT 30
        """,
        (application_id,),
    )
    history = []
    for row in rows:
        reviewer = (row["display_name"] or row["username"] or "System").strip()
        callsign = (row["callsign"] or "").strip()
        history.append({
            "reviewer": f"{reviewer} {bracket_callsign(callsign)}".strip() if callsign else reviewer,
            "event_type": str(row["event_type"]),
            "decision": str(row["decision"]),
            "note": str(row["note"]),
            "created_at": str(row["created_at"]),
        })
    return history


def application_voter_count(application_type: str) -> int:
    ranks = DIVISION_APPLICATION_REVIEW_RANKS.get(application_type)
    if not ranks:
        return 0
    row = row_one(
        "SELECT COUNT(*) AS count FROM supervisor_users WHERE role IN ('owner', 'admin') OR division_rank IN (?, ?) OR additional_division_rank IN (?, ?)",
        (*tuple(ranks), *tuple(ranks)),
    )
    return int(row["count"] if row else 0)


def application_vote_summary(application_id: int, application_type: str) -> dict[str, int]:
    ranks = DIVISION_APPLICATION_REVIEW_RANKS.get(application_type)
    if not ranks:
        return {"yes_votes": 0, "no_votes": 0, "abstain_votes": 0, "total_votes": 0}
    row = row_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN application_votes.vote = 'yes' THEN 1 ELSE 0 END), 0) AS yes_votes,
            COALESCE(SUM(CASE WHEN application_votes.vote = 'no' THEN 1 ELSE 0 END), 0) AS no_votes,
            COALESCE(SUM(CASE WHEN application_votes.vote = 'abstain' THEN 1 ELSE 0 END), 0) AS abstain_votes,
            COUNT(application_votes.id) AS total_votes
        FROM application_votes
        JOIN supervisor_users ON supervisor_users.id = application_votes.user_id
        WHERE application_votes.application_id = ?
          AND (supervisor_users.role IN ('owner', 'admin') OR supervisor_users.division_rank IN (?, ?) OR supervisor_users.additional_division_rank IN (?, ?))
        """,
        (application_id, *tuple(ranks), *tuple(ranks)),
    )
    return {
        "yes_votes": int(row["yes_votes"] if row else 0),
        "no_votes": int(row["no_votes"] if row else 0),
        "abstain_votes": int(row["abstain_votes"] if row else 0),
        "total_votes": int(row["total_votes"] if row else 0),
    }


def finalize_application_if_ready(application_id: int) -> str:
    application = row_one("SELECT application_type FROM applications WHERE id = ?", (application_id,))
    if application is None:
        return "pending"
    application_type = str(application["application_type"])
    summary = application_vote_summary(application_id, application_type)
    required_votes = application_voter_count(application_type)
    if required_votes <= 0 or summary["total_votes"] < required_votes:
        return "pending"
    status = "accepted" if summary["yes_votes"] > summary["no_votes"] else "denied"
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE applications
            SET decision_status = ?, decided_at = ?
            WHERE id = ? AND decision_status = 'pending'
            """,
            (status, now_text(), application_id),
        )
        if cursor.rowcount:
            db.execute(
                "INSERT INTO application_review_events (application_id, event_type, decision, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (application_id, "finalized", status, "Voting threshold reached.", now_text()),
            )
    return status


def supervisor_fto_evaluations() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM fto_evaluations
        ORDER BY id DESC
        LIMIT 100
        """
    )


def division_training_logs() -> list[dict[str, object]]:
    rows = rows_all(
        """
        SELECT division_training_logs.*, supervisor_users.username AS trainer_username
        FROM division_training_logs
        LEFT JOIN supervisor_users ON supervisor_users.id = division_training_logs.trainer_user_id
        ORDER BY division_training_logs.id DESC
        LIMIT 120
        """
    )
    logs = []
    for row in rows:
        log = dict(row)
        log["departments"] = json.loads(row["departments_json"])
        logs.append(log)
    return logs


def division_certification_logs() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM division_certification_logs
        ORDER BY id DESC
        LIMIT 120
        """
    )


def dashboard_announcements() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM dashboard_announcements
        ORDER BY id DESC
        LIMIT 25
        """
    )


def dashboard_events() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM dashboard_events
        ORDER BY event_date ASC,
                 CASE WHEN COALESCE(event_time, '') = '' THEN 1 ELSE 0 END ASC,
                 event_time ASC, id DESC
        """
    )


def local_today() -> date:
    try:
        return datetime.now(ZoneInfo(app.config["APP_TIMEZONE"])).date()
    except (KeyError, ValueError):
        return date.today()


def calendar_timezone(timezone_name: str) -> tuple[str, ZoneInfo]:
    try:
        return timezone_name, ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        fallback = app.config["APP_TIMEZONE"]
        return fallback, ZoneInfo(fallback)


def dashboard_calendar(events: list[sqlite3.Row], requested_month: str = "", timezone_name: str = "") -> dict[str, object]:
    viewer_timezone_name, viewer_timezone = calendar_timezone(timezone_name or app.config["APP_TIMEZONE"])
    today = datetime.now(viewer_timezone).date()
    try:
        viewed_month = datetime.strptime(requested_month, "%Y-%m").date().replace(day=1) if requested_month else today.replace(day=1)
    except ValueError:
        viewed_month = today.replace(day=1)
    previous_month = (viewed_month - timedelta(days=1)).replace(day=1)
    next_month = (viewed_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(viewed_month.year, viewed_month.month)
    events_by_date: dict[str, list[sqlite3.Row]] = {}
    for event in events:
        try:
            if event["event_start_utc"]:
                local_start = datetime.fromisoformat(event["event_start_utc"]).astimezone(viewer_timezone)
                event_day = local_start.date()
                localized_event = dict(event)
                localized_event["display_time"] = local_start.strftime("%I:%M %p").lstrip("0")
                localized_event["local_iso"] = local_start.isoformat()
            else:
                event_day = datetime.strptime(event["event_date"], "%Y-%m-%d").date()
                localized_event = dict(event)
                localized_event["display_time"] = "Time TBD"
                localized_event["local_iso"] = ""
        except (ValueError, TypeError):
            continue
        events_by_date.setdefault(event_day.isoformat(), []).append(localized_event)
    for day_events in events_by_date.values():
        day_events.sort(key=lambda item: (not bool(item["local_iso"]), item["local_iso"] or "", item["id"]))
    weeks = []
    for week in month_weeks:
        weeks.append([
            {
                "day": cell.day,
                "iso_date": cell.isoformat(),
                "label": cell.strftime("%A, %B %d, %Y").replace(" 0", " "),
                "in_month": cell.month == viewed_month.month,
                "is_today": cell == today,
                "events": events_by_date.get(cell.isoformat(), []),
            }
            for cell in week
        ])
    month_prefix = viewed_month.strftime("%Y-%m")
    month_events = [event for iso_date, items in events_by_date.items() if iso_date.startswith(month_prefix) for event in items]
    return {
        "month_name": viewed_month.strftime("%B"),
        "year": viewed_month.year,
        "month_value": viewed_month.strftime("%Y-%m"),
        "is_current_month": viewed_month.year == today.year and viewed_month.month == today.month,
        "today_iso": today.isoformat(),
        "weeks": weeks,
        "month_events": month_events,
        "previous_month": previous_month.strftime("%Y-%m"),
        "next_month": next_month.strftime("%Y-%m"),
        "timezone": viewer_timezone_name,
    }


def hub_dashboard_stats(announcements: list[sqlite3.Row], events: list[sqlite3.Row]) -> dict[str, int]:
    today_text = local_today().isoformat()
    upcoming_events = [event for event in events if event["event_date"] >= today_text]
    return {
        "on_duty": sum(1 for row in all_cadet_timestamps() if not row["stop_utc"]),
        "active": len(active_cadets()),
        "training": len(upcoming_events),
        "announcements": len(announcements),
    }


def hub_notifications(user: sqlite3.Row, announcements: list[sqlite3.Row]) -> list[dict[str, str]]:
    """Return only role-relevant action items for the Hub notification center."""
    notifications: list[dict[str, str]] = []
    display_name = (user["display_name"] or user["username"] or "").strip()
    callsign = (user["callsign"] or "").strip()
    dismissed = {
        str(row["notification_key"])
        for row in rows_all("SELECT notification_key FROM notification_dismissals WHERE user_id = ?", (user["id"],))
    }

    for row in rows_all(
        """
        SELECT user_notifications.*, cadet_timestamps.cadet_name, cadet_timestamps.cadet_callsign
        FROM user_notifications
        LEFT JOIN cadet_timestamps ON cadet_timestamps.id = user_notifications.timestamp_id
        WHERE user_notifications.user_id = ? AND user_notifications.read_at IS NULL
        ORDER BY user_notifications.id DESC
        LIMIT 4
        """,
        (user["id"],),
    ):
        timestamp_id = row["timestamp_id"]
        notification_url = ""
        if timestamp_id:
            notification_url = url_for("fto_panel", embed=1, timestamp_reminder=timestamp_id, _anchor="fto-active-tab")
        notifications.append({
            "tone": "review",
            "label": "FTP timestamp reminder",
            "title": str(row["title"]),
            "detail": str(row["body"]),
            "time": str(row["created_at"]),
            "panel": "FTP - Active Cadets",
            "url": notification_url,
            "key": f"timestamp-reminder:{row['id']}",
            "persistent_id": str(row["id"]),
        })

    application_checks = []
    application_args: list[str] = []
    if callsign:
        application_checks.append("callsign = ?")
        application_args.append(callsign)
    if display_name:
        application_checks.append("full_name = ?")
        application_args.append(display_name)
    if application_checks:
        for row in rows_all(
            f"""
            SELECT id, application_type, decision_status, decided_at, created_at
            FROM applications
            WHERE decision_status IN ('accepted', 'denied') AND ({' OR '.join(application_checks)})
            ORDER BY COALESCE(decided_at, created_at) DESC
            LIMIT 2
            """,
            tuple(application_args),
        ):
            accepted = row["decision_status"] == "accepted"
            division = str(row["application_type"]).upper()
            notifications.append({
                "tone": "success" if accepted else "decision",
                "label": "Application decision",
                "title": f"{division} application {'accepted' if accepted else 'declined'}",
                "detail": "Your application has been approved." if accepted else "Your application has been reviewed.",
                "time": str(row["decided_at"] or row["created_at"]),
                "panel": "",
                "key": f"application-decision:{row['id']}",
                "persistent_id": "",
            })

    if user["role"] in SUPERVISOR_ROLES:
        pending_requests = table_count("test_requests", "WHERE status = 'pending'")
        if pending_requests:
            notifications.append({
                "tone": "review",
                "label": "Approval queue",
                "title": f"{pending_requests} exam request{'s' if pending_requests != 1 else ''} awaiting review",
                "detail": "Open Test Requests to approve or decline access.",
                "time": "Needs attention",
                "panel": "Supervisor - Test Requests",
                "key": f"exam-review-queue:{pending_requests}:{row_one('SELECT MAX(id) AS id FROM test_requests WHERE status = \'pending\'')['id'] or 0}",
                "persistent_id": "",
            })

    for application_type, panel_title, label in (
        ("ert", "ERT - Applications", "ERT"),
        ("ftp", "FTP - Applications", "FTP"),
    ):
        if user_can_review_division_applications(application_type, user):
            pending = table_count("applications", "WHERE application_type = ? AND decision_status = 'pending'", (application_type,))
            if pending:
                notifications.append({
                    "tone": "review",
                    "label": "Application decision",
                    "title": f"{pending} {label} application{'s' if pending != 1 else ''} awaiting a decision",
                    "detail": "Open the application queue to review and record your vote.",
                    "time": "Needs attention",
                    "panel": panel_title,
                    "key": f"{application_type}-review-queue:{pending}:{row_one('SELECT MAX(id) AS id FROM applications WHERE application_type = ? AND decision_status = \'pending\'', (application_type,))['id'] or 0}",
                    "persistent_id": "",
                })

    return [notification for notification in notifications if notification["key"] not in dismissed][:8]


def database_file_info() -> dict[str, str]:
    database_path = Path(app.config["DATABASE"])
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    if not database_path.exists():
        return {"path": str(database_path), "size": "Missing", "modified": "Not found"}
    size_mb = database_path.stat().st_size / (1024 * 1024)
    modified = datetime.fromtimestamp(database_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return {"path": str(database_path), "size": f"{size_mb:.2f} MB", "modified": modified}


def table_count(table_name: str, where_clause: str = "", args: tuple = ()) -> int:
    query = f"SELECT COUNT(*) AS count FROM {table_name} {where_clause}"
    row = row_one(query, args)
    return int(row["count"] if row else 0)


def run_git_command(*args: str) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Unavailable"
    output = (result.stdout or result.stderr).strip()
    return output if output else "Unavailable"


def clean_git_output(value: str) -> str:
    if value == "Unavailable" or value.lower().startswith("error:"):
        return "Unavailable"
    return value


def dev_panel_data() -> dict[str, object]:
    data_tables = (
        "supervisor_users",
        "login_events",
        "applications",
        "application_votes",
        "submissions",
        "test_requests",
        "cadets",
        "cadet_logs",
        "cadet_timestamps",
        "complaints",
        "dashboard_announcements",
        "dashboard_events",
        "division_training_logs",
        "division_certification_logs",
    )
    table_counts = {table: table_count(table) for table in data_tables}
    table_schema = []
    with db_connection() as db:
        for table_name in data_tables:
            columns = db.execute(f"PRAGMA table_info({table_name})").fetchall()
            table_schema.append(
                {
                    "name": table_name,
                    "rows": table_counts[table_name],
                    "columns": [column["name"] for column in columns],
                    "primary_keys": [column["name"] for column in columns if column["pk"]],
                }
            )
    recent_logins = rows_all(
        """
        SELECT login_events.*, supervisor_users.phone_number
        FROM login_events
        LEFT JOIN supervisor_users ON supervisor_users.id = login_events.user_id
        ORDER BY login_events.id DESC
        LIMIT 10
        """
    )
    render_commit = os.environ.get("RENDER_GIT_COMMIT", "")
    render_branch = os.environ.get("RENDER_GIT_BRANCH", "")
    render_service = os.environ.get("RENDER_SERVICE_NAME", "")
    git_branch = clean_git_output(run_git_command("branch", "--show-current"))
    git_commit = clean_git_output(run_git_command("rev-parse", "--short", "HEAD"))
    git_status = clean_git_output(run_git_command("status", "--short"))
    git_recent_commits = clean_git_output(run_git_command("log", "--oneline", "-5"))
    git_remote = clean_git_output(run_git_command("remote", "get-url", "origin"))
    if git_branch == "Unavailable":
        git_branch = render_branch or ("Hosted deploy" if render_commit else "Unavailable")
    if git_commit == "Unavailable" and render_commit:
        git_commit = render_commit[:7]
    if git_remote == "Unavailable" and render_commit:
        git_remote = "Managed by Render"
    if git_recent_commits == "Unavailable" and render_commit:
        git_recent_commits = f"{render_commit[:7]} Current Render deploy"
    if git_status == "Unavailable":
        git_status_label = "Hosted deploy" if render_commit else "Unavailable"
    elif not git_status:
        git_status_label = "Clean"
    else:
        git_status_label = git_status
    template_files = sorted(Path("templates").glob("*.html"))
    static_files = [path for path in Path("static").rglob("*") if path.is_file()]
    static_total_mb = sum(path.stat().st_size for path in static_files) / (1024 * 1024)
    static_types = {
        "css": sum(1 for path in static_files if path.suffix == ".css"),
        "js": sum(1 for path in static_files if path.suffix == ".js"),
        "images": sum(1 for path in static_files if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}),
    }
    largest_static = sorted(static_files, key=lambda path: path.stat().st_size, reverse=True)[:8]
    route_rows = sorted(
        (
            {
                "endpoint": str(rule.endpoint),
                "rule": str(rule.rule),
                "methods": ", ".join(sorted(method for method in rule.methods if method not in {"HEAD", "OPTIONS"})),
            }
            for rule in app.url_map.iter_rules()
            if not str(rule.endpoint).startswith("static")
        ),
        key=lambda row: (row["rule"], row["endpoint"]),
    )
    safe_env_keys = (
        "DATABASE_URL",
        "DOC_APPLICATION_URL",
        "COMPLAINTS_URL",
        "OWNER_USERNAME",
        "RENDER_GIT_COMMIT",
        "RENDER_SERVICE_NAME",
        "SECRET_KEY",
        "SUPERVISOR_PASSWORD",
    )
    env_flags = {
        key: ("Set" if os.environ.get(key) else "Not set")
        for key in safe_env_keys
    }
    account_total = table_count("supervisor_users")
    if not os.environ.get("SUPERVISOR_PASSWORD") and account_total:
        env_flags["SUPERVISOR_PASSWORD"] = "Seed only - account exists"
    warnings = []
    if app.config["SECRET_KEY"] == "dev-change-me":
        warnings.append("SECRET_KEY is still using the default development value.")
    if app.config["DATABASE"] == "instance/doc_quiz.db":
        warnings.append("Database is using the local instance path.")
    if git_status_label not in {"Clean", "Unavailable", "Hosted deploy"}:
        warnings.append("Local Git has uncommitted changes.")
    if env_flags["SUPERVISOR_PASSWORD"] == "Not set":
        warnings.append("SUPERVISOR_PASSWORD is not set in the environment.")
    if not warnings:
        warnings.append("No major warnings found.")
    return {
        "database": database_file_info(),
        "table_counts": table_counts,
        "table_schema": table_schema,
        "recent_logins": recent_logins,
        "warnings": warnings,
        "env_flags": env_flags,
        "assets": {
            "templates": len(template_files),
            "static_files": len(static_files),
            "static_size": f"{static_total_mb:.2f} MB",
            "types": static_types,
            "largest_static": [
                {"path": str(path).replace("\\", "/"), "size": f"{path.stat().st_size / 1024:.1f} KB"}
                for path in largest_static
            ],
        },
        "git": {
            "branch": git_branch,
            "commit": git_commit,
            "status": git_status_label,
            "deploy_commit": render_commit or "Local",
            "remote": git_remote,
            "recent_commits": git_recent_commits,
            "service": render_service or "Local",
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "database_url": app.config["DATABASE"],
            "static_version": STATIC_VERSION,
            "routes": len(route_rows),
            "server_time": now_text(),
            "secret_key": "Set" if app.config["SECRET_KEY"] != "dev-change-me" else "Default dev key",
            "division_rank_labels": DIVISION_RANK_LABELS,
        },
        "routes": route_rows,
    }


def record_login_event(user: sqlite3.Row) -> None:
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    ip_address = forwarded_for or request.remote_addr or ""
    user_agent = request.headers.get("User-Agent", "")
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO login_events (
                user_id, username, display_name, callsign, role, division_rank,
                ip_address, user_agent, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                user["username"],
                user["display_name"] or "",
                user["callsign"] or "",
                user["role"],
                user["division_rank"] or "",
                ip_address,
                user_agent[:240],
                now_text(),
            ),
        )


def certification_target_users() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT id, username, display_name, callsign, role, division_rank, additional_division_rank
        FROM supervisor_users
        WHERE role != 'owner'
        ORDER BY display_name, username
        """
    )


def supervisor_users() -> list[sqlite3.Row]:
    return rows_all("SELECT id, username, display_name, callsign, phone_number, role, division_rank, additional_division_rank, created_at FROM supervisor_users ORDER BY role, display_name, username")


def supervisor_users_by_role() -> dict[str, list[sqlite3.Row]]:
    users = supervisor_users()
    return {role: [user for user in users if user["role"] == role] for role in USER_ROLES}


def roster_timestamp_name(user: sqlite3.Row) -> str:
    display_name = (user["display_name"] or user["username"]).strip()
    callsign = (user["callsign"] or "").strip()
    if callsign:
        return f"{bracket_callsign(callsign)} {display_name}"
    return display_name


def split_timestamp_officer(value: str) -> tuple[str, str]:
    officer = value.strip()
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", officer)
    if not match:
        return officer, ""
    return match.group(2).strip(), match.group(1).strip()


def timestamp_ftos() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for user in supervisor_users():
        if not user_can_access_ftp(user):
            continue
        name = roster_timestamp_name(user)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "id": None,
                "name": name,
                "display_name": (user["display_name"] or user["username"]).strip(),
                "callsign": bracket_callsign(user["callsign"]) if user["callsign"] else "",
                "created_at": user["created_at"],
                "source": "roster",
                "role": user["role"],
                "division_rank": user["division_rank"],
                "removable": False,
            }
        )
    for row in rows_all("SELECT id, name, created_at FROM timestamp_ftos ORDER BY name"):
        key = row["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "id": row["id"],
                "name": row["name"],
                "display_name": row["name"],
                "callsign": "",
                "created_at": row["created_at"],
                "source": "extra",
                "role": "",
                "division_rank": "",
                "removable": True,
            }
        )
    return sorted(entries, key=lambda item: str(item["name"]).casefold())


def timestamp_fto_names() -> list[str]:
    return [str(row["name"]) for row in timestamp_ftos()]


def cadet_timestamp_officers(user: sqlite3.Row, cadet_record: sqlite3.Row | None = None) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for account in supervisor_users():
        display_name = (account["display_name"] or account["username"]).strip()
        callsign = bracket_callsign(account["callsign"]) if account["callsign"] else ""
        entries.append(
            {
                "id": account["id"],
                "name": roster_timestamp_name(account),
                "display_name": display_name,
                "callsign": callsign,
                "role": str(account["role"]).title(),
            }
        )
    return sorted(entries, key=lambda entry: (str(entry["display_name"]).casefold(), str(entry["callsign"]).casefold()))


def cadet_timestamp_options(user: sqlite3.Row, cadet_record: sqlite3.Row | None = None) -> list[str]:
    return [str(entry["name"]) for entry in cadet_timestamp_officers(user, cadet_record)]


def active_cadets() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT cadets.*,
               (SELECT COUNT(*) FROM cadet_logs WHERE cadet_logs.cadet_id = cadets.id) AS log_count,
               (SELECT MAX(created_at) FROM cadet_logs WHERE cadet_logs.cadet_id = cadets.id) AS last_log_at
        FROM cadets
        WHERE status = 'Active'
        ORDER BY badge_number
        """
    )


def cadet_record_for_user(user: sqlite3.Row) -> sqlite3.Row | None:
    callsign_key = cadet_timestamp_key(user["callsign"])
    name_key = cadet_timestamp_key(user["display_name"] or user["username"])
    for cadet in active_cadets():
        if callsign_key and cadet_timestamp_key(cadet["badge_number"]) == callsign_key:
            return cadet
        if name_key and cadet_timestamp_key(cadet["name"]) == name_key:
            return cadet
    return None


def archived_cadets() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT cadets.*,
               (SELECT COUNT(*) FROM cadet_logs WHERE cadet_logs.cadet_id = cadets.id) AS log_count,
               (SELECT MAX(created_at) FROM cadet_logs WHERE cadet_logs.cadet_id = cadets.id) AS last_log_at
        FROM cadets
        WHERE status IN ('Archived', 'Removed')
        ORDER BY badge_number
        """
    )


def cadet_logs(cadet_id: int) -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM cadet_logs
        WHERE cadet_id = ?
        ORDER BY id DESC
        """,
        (cadet_id,),
    )


def cadet_timestamp_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def cadet_timestamps_by_cadet(cadets: list[sqlite3.Row], timestamps: list[sqlite3.Row]) -> dict[int, list[sqlite3.Row]]:
    grouped: dict[int, list[sqlite3.Row]] = {cadet["id"]: [] for cadet in cadets}
    for cadet in cadets:
        badge_key = cadet_timestamp_key(cadet["badge_number"])
        name_key = cadet_timestamp_key(cadet["name"])
        for timestamp in timestamps:
            timestamp_callsign_key = cadet_timestamp_key(timestamp["cadet_callsign"])
            timestamp_name_key = cadet_timestamp_key(timestamp["cadet_name"])
            if timestamp_callsign_key == badge_key or timestamp_name_key == name_key:
                grouped[cadet["id"]].append(timestamp)
    return grouped


def timestamp_matches_cadet(cadet: sqlite3.Row, timestamp: sqlite3.Row) -> bool:
    return (
        cadet_timestamp_key(timestamp["cadet_callsign"]) == cadet_timestamp_key(cadet["badge_number"])
        or cadet_timestamp_key(timestamp["cadet_name"]) == cadet_timestamp_key(cadet["name"])
    )


def active_timestamp_for_managed_cadet(cadet_id: int) -> sqlite3.Row | None:
    cadet = row_one("SELECT * FROM cadets WHERE id = ?", (cadet_id,))
    if cadet is None:
        return None
    for timestamp in all_cadet_timestamps():
        if not timestamp["stop_utc"] and timestamp_matches_cadet(cadet, timestamp):
            return timestamp
    return None


def cadet_timestamp_logs(user_id: int) -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT *
        FROM cadet_timestamps
        WHERE cadet_user_id = ? AND archived_at IS NULL
        ORDER BY id DESC
        LIMIT 60
        """,
        (user_id,),
    )


def active_cadet_timestamp(user_id: int) -> sqlite3.Row | None:
    return row_one(
        """
        SELECT *
        FROM cadet_timestamps
        WHERE cadet_user_id = ? AND stop_utc IS NULL AND archived_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )


def all_cadet_timestamps() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT cadet_timestamps.*, supervisor_users.username AS account_username
        FROM cadet_timestamps
        LEFT JOIN supervisor_users ON supervisor_users.id = cadet_timestamps.cadet_user_id
        WHERE cadet_timestamps.archived_at IS NULL
        ORDER BY cadet_timestamps.id DESC
        LIMIT 120
        """
    )


def archived_cadet_timestamps() -> list[sqlite3.Row]:
    return rows_all(
        """
        SELECT cadet_timestamps.*, supervisor_users.username AS account_username
        FROM cadet_timestamps
        LEFT JOIN supervisor_users ON supervisor_users.id = cadet_timestamps.cadet_user_id
        WHERE cadet_timestamps.archived_at IS NOT NULL
        ORDER BY cadet_timestamps.archived_at DESC, cadet_timestamps.id DESC
        LIMIT 120
        """
    )


def timestamp_assigned_ftp_member(timestamp: sqlite3.Row | dict[str, object]) -> sqlite3.Row | None:
    """Resolve the FTP account named on a timestamp, when that roster entry is linked to an account."""
    fto_name = str(timestamp["fto_name"] if isinstance(timestamp, sqlite3.Row) else timestamp.get("fto_name", ""))
    display_name, callsign = split_timestamp_officer(fto_name)
    display_key = cadet_timestamp_key(display_name)
    callsign_key = cadet_timestamp_key(callsign)
    for account in supervisor_users():
        if not user_can_access_ftp(account):
            continue
        account_name_key = cadet_timestamp_key(account["display_name"] or account["username"])
        account_callsign_key = cadet_timestamp_key(account["callsign"])
        if (callsign_key and callsign_key == account_callsign_key) or (display_key and display_key == account_name_key):
            return account
    return None


def supervisor_ftp_timestamp_followups() -> list[dict[str, object]]:
    """Build the supervisor view of FTP timestamps and their corresponding operation report."""
    timestamps = rows_all(
        """
        SELECT cadet_timestamps.*, supervisor_users.username AS account_username
        FROM cadet_timestamps
        LEFT JOIN supervisor_users ON supervisor_users.id = cadet_timestamps.cadet_user_id
        ORDER BY cadet_timestamps.id DESC
        LIMIT 240
        """
    )
    logs_by_timestamp = {
        int(row["timestamp_id"]): dict(row)
        for row in rows_all("SELECT * FROM cadet_logs WHERE timestamp_id IS NOT NULL ORDER BY id DESC")
    }
    cadets = rows_all("SELECT id, badge_number, name FROM cadets")
    followups: list[dict[str, object]] = []
    for timestamp in timestamps:
        assignee = timestamp_assigned_ftp_member(timestamp)
        if assignee is None:
            continue
        matching_cadet = next((cadet for cadet in cadets if timestamp_matches_cadet(cadet, timestamp)), None)
        operation = logs_by_timestamp.get(int(timestamp["id"]))
        followups.append({
            "timestamp": dict(timestamp),
            "cadet_id": int(matching_cadet["id"]) if matching_cadet else None,
            "assignee_id": int(assignee["id"]),
            "assignee_name": roster_timestamp_name(assignee),
            "operation": operation,
            "can_remind": bool(timestamp["stop_utc"] and matching_cadet and operation is None),
        })
    return followups


def timestamp_payload(row: sqlite3.Row) -> dict[str, object]:
    paused_utc = row["paused_utc"] if "paused_utc" in row.keys() else None
    paused_total_seconds = row["paused_total_seconds"] if "paused_total_seconds" in row.keys() else 0
    return {
        "id": row["id"],
        "cadet_name": row["cadet_name"],
        "cadet_callsign": bracket_callsign(row["cadet_callsign"]),
        "account_username": row["account_username"] if "account_username" in row.keys() else None,
        "creator_name": row["creator_name"] if "creator_name" in row.keys() else "",
        "creator_callsign": bracket_callsign(row["creator_callsign"]) if "creator_callsign" in row.keys() else "",
        "fto_name": row["fto_name"],
        "start_utc": row["start_utc"],
        "start_timezone": row["start_timezone"],
        "stop_utc": row["stop_utc"],
        "stop_timezone": row["stop_timezone"],
        "paused_utc": paused_utc,
        "paused_total_seconds": paused_total_seconds or 0,
        "is_paused": bool(paused_utc and not row["stop_utc"]),
        "archived_at": row["archived_at"] if "archived_at" in row.keys() else None,
        "created_at": row["created_at"],
    }


def request_payload(request_row: sqlite3.Row) -> dict[str, object]:
    expires_at = test_request_expires_at(request_row)
    seconds_remaining = test_request_seconds_remaining(request_row)
    return {
        "id": request_row["id"],
        "quiz_slug": request_row["quiz_slug"],
        "quiz_title": request_row["quiz_title"],
        "trainee_name": request_row["trainee_name"],
        "callsign": request_row["callsign"],
        "status": request_row["status"],
        "created_at": request_row["created_at"],
        "reviewed_at": request_row["reviewed_at"],
        "reviewed_by": request_row["reviewed_by"],
        "started_at": request_row["started_at"],
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else "",
        "seconds_remaining": seconds_remaining,
        "expires_label": f"{seconds_remaining // 60}:{seconds_remaining % 60:02d}" if request_row["status"] == "pending" else "",
    }


def complaint_payload(complaint: sqlite3.Row) -> dict[str, object]:
    return {
        "id": complaint["id"],
        "discord_username": complaint["discord_username"],
        "name": complaint["name"],
        "contact_info": complaint["contact_info"],
        "officer_reported": complaint["officer_reported"],
        "topic_datetime": complaint["topic_datetime"],
        "event_details": complaint["event_details"],
        "evidence": complaint["evidence"],
        "witnesses": complaint["witnesses"],
        "additional_info": complaint["additional_info"],
        "created_at": complaint["created_at"],
    }


def application_payload(application: dict[str, object]) -> dict[str, object]:
    application_type = str(application["application_type"])
    form = APPLICATION_FORMS.get(application_type, {})
    return {
        "id": application["id"],
        "application_type": application_type,
        "application_title": form.get("short_title", application_type.upper()),
        "discord_username": application["discord_username"],
        "full_name": application["full_name"],
        "callsign": application["callsign"],
        "rank": application["rank"],
        "responses": application["responses"],
        "yes_votes": application.get("yes_votes", 0),
        "no_votes": application.get("no_votes", 0),
        "abstain_votes": application.get("abstain_votes", 0),
        "total_votes": application.get("total_votes", 0),
        "required_votes": application.get("required_votes", 0),
        "decision_status": application.get("decision_status", "pending"),
        "decided_at": application.get("decided_at"),
        "is_final": application.get("is_final", False),
        "can_command_override": application.get("can_command_override", False),
        "user_vote": application.get("user_vote", ""),
        "created_at": application["created_at"],
    }


def quiz_options() -> list[dict[str, str]]:
    return [{"slug": quiz.slug, "title": quiz.title} for quiz in QUIZZES]


def builtin_question_rows() -> list[dict[str, object]]:
    originals: dict[str, Question] = {}
    quiz_names: dict[str, list[str]] = {}
    for quiz in QUIZZES:
        for question in base_questions_for_quiz(quiz):
            key = question.source_key or stable_question_key(question)
            originals.setdefault(key, question)
            quiz_names.setdefault(key, [])
            if quiz.title not in quiz_names[key]:
                quiz_names[key].append(quiz.title)

    overrides = question_overrides()
    rows = []
    for key, original in originals.items():
        current = apply_question_overrides((original,))[0]
        rows.append(
            {
                "key": key,
                "quizzes": ", ".join(quiz_names[key]),
                "prompt": current.prompt,
                "answer": current.answer,
                "choices_text": "\n".join(current.choices),
                "category": current.category,
                "written": current.written,
                "is_customized": key in overrides,
            }
        )
    return sorted(rows, key=lambda row: (str(row["quizzes"]), str(row["category"]), str(row["prompt"])))


def builtin_answer_key_groups() -> list[dict[str, object]]:
    groups = []
    overrides = question_overrides()
    for quiz in QUIZZES:
        seen: set[str] = set()
        questions = []
        for original in base_questions_for_quiz(quiz):
            key = original.source_key or stable_question_key(original)
            if key in seen:
                continue
            seen.add(key)
            override = overrides.get(key)
            current = (
                Question(
                    override["prompt"],
                    override["answer"],
                    tuple(json.loads(override["choices_json"])),
                    override["category"],
                    written=bool(override["written"]),
                    aliases=original.aliases,
                    source_key=key,
                )
                if override is not None
                else original
            )
            questions.append(
                {
                    "key": key,
                    "prompt": current.prompt,
                    "answer": current.answer,
                    "choices": list(current.choices),
                    "category": current.category,
                    "written": current.written,
                    "is_customized": key in overrides,
                }
            )
        groups.append(
            {
                "slug": quiz.slug,
                "title": quiz.title,
                "summary": quiz.summary,
                "kind": "Exam" if quiz.slug in CERT_QUIZZES else "Training",
                "question_count": len(questions),
                "questions": questions,
            }
        )
    for category in training_categories_with_overrides():
        questions = []
        for index, question in enumerate(category["questions"]):
            key = f"training:{category['slug']}:{index}"
            written = question["type"] == "written"
            questions.append(
                {
                    "key": key,
                    "prompt": question["prompt"],
                    "answer": question.get("guidance", "") if written else question.get("answer", ""),
                    "choices": [] if written else list(question.get("options", [])),
                    "category": category["title"],
                    "written": written,
                    "is_customized": key in overrides,
                }
            )
        groups.append(
            {
                "slug": f"topic-{category['slug']}",
                "title": category["title"],
                "summary": category["summary"],
                "kind": "Training",
                "question_count": len(questions),
                "questions": questions,
            }
        )
    return groups


def training_categories_with_overrides() -> list[dict[str, object]]:
    overrides = question_overrides()
    categories: list[dict[str, object]] = []
    for category in CADET_TRAINING_CATEGORIES:
        questions = []
        for index, original in enumerate(category["questions"]):
            key = f"training:{category['slug']}:{index}"
            override = overrides.get(key)
            question = dict(original)
            if override is not None:
                question["prompt"] = override["prompt"]
                if question["type"] == "written":
                    question["guidance"] = override["answer"]
                else:
                    question["answer"] = override["answer"]
                    question["options"] = json.loads(override["choices_json"])
            questions.append(question)
        categories.append({**category, "questions": questions})
    return categories


def cleaned_choices(answer: str, choices_text: str, written: bool) -> list[str]:
    if written:
        return [answer]
    choices = [line.strip() for line in choices_text.splitlines() if line.strip()]
    if answer not in choices:
        choices.insert(0, answer)
    return choices


def question_description(question: Question) -> str:
    descriptions = {
        "10-Codes": "Radio code knowledge. Written answers can use close wording, common aliases, or minor typo fixes.",
        "Radio": "Radio procedure and communication discipline from the DOC SOP.",
        "Command": "Rank structure, authority, and chain-of-command knowledge.",
        "Rules": "General DOC conduct and decision-making expectations.",
        "Divisions": "DOC division purpose and identification knowledge.",
        "Equipment": "Authorized gear, certifications, and rank-restricted equipment.",
        "Weapons": "Weapon rules, storage expectations, and force restrictions.",
        "Vehicles": "Authorized vehicle use and emergency driving limits.",
        "Transport": "Prisoner movement, cuffs, PD coordination, and gate procedures.",
        "Risk Levels": "Prison risk levels and what each level authorizes.",
        "Use of Force": "Force escalation and when lethal force is allowed.",
        "Visitation": "Visitor entry, search rules, and restricted visitation procedures.",
        "Riot Procedures": "How to respond to riots, prison breaks, and lockdowns.",
        "Inmates": "Searches, classification, jail time changes, and inmate control.",
        "HUT": "Held Until Trial inmate restrictions and DOJ jurisdiction.",
        "Maximum Security": "Maximum Security restrictions and escort requirements.",
        "Reports": "When and how DOC incident reports should be made.",
        "Conduct": "Professional conduct at public locations and partner agencies.",
        "Activity": "Activity requirements, promotion eligibility, and LOA expectations.",
    }
    mode = "Written answer" if question.written else "Multiple choice"
    return f"{mode}. {descriptions.get(question.category, 'DOC SOP knowledge check.')}"


def question_payload(index: int, question: Question) -> dict[str, object]:
    choices = list(question.choices)
    random.shuffle(choices)
    return {
        "id": index,
        "prompt": question.prompt,
        "description": question_description(question),
        "answer": question.answer,
        "choices": choices,
        "category": question.category,
        "written": question.written,
    }


def normalize_answer(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def close_written_match(selected: str, answer: str) -> bool:
    if not selected or not answer:
        return False
    if selected in answer or answer in selected:
        return True
    selected_words = set(selected.split())
    answer_words = set(answer.split())
    if len(answer_words) > 1 and answer_words.issubset(selected_words):
        return True
    return SequenceMatcher(None, selected, answer).ratio() >= 0.84


def keyword_written_match(selected: str, answer: str) -> bool:
    selected_words = set(selected.split())
    for keyword_group in ANSWER_KEYWORDS.get(answer, ()):
        normalized_group = {normalize_answer(word) for word in keyword_group}
        if normalized_group.issubset(selected_words):
            return True
    return False


def answer_matches(selected: str, question: Question) -> bool:
    selected_norm = normalize_answer(selected)
    possible = (question.answer, *question.aliases)
    for answer in possible:
        answer_norm = normalize_answer(answer)
        if selected_norm == answer_norm:
            return True
        if question.written:
            if keyword_written_match(selected_norm, answer):
                return True
            if close_written_match(selected_norm, answer_norm):
                return True
    return False


def attempt_key(slug: str) -> str:
    return f"attempt:{slug}"


def request_key(slug: str) -> str:
    return f"request:{slug}"


def new_attempt(slug: str, questions: tuple[Question, ...], question_total: int | None = None) -> dict[str, object]:
    order = list(range(len(questions)))
    random.shuffle(order)
    if question_total is not None:
        order = order[:question_total]
    attempt = {"order": order, "position": 0, "responses": [], "bank_size": len(questions)}
    session[attempt_key(slug)] = attempt
    session.modified = True
    return attempt


def current_attempt(
    slug: str,
    questions: tuple[Question, ...],
    question_total: int | None = None,
    *,
    create_if_missing: bool = True,
) -> dict[str, object] | None:
    attempt = session.get(attempt_key(slug))
    expected_total = question_total or len(questions)
    if not attempt or attempt.get("bank_size") != len(questions) or len(attempt.get("order", [])) != expected_total:
        return new_attempt(slug, questions, expected_total) if create_if_missing else None
    return attempt


def save_submission(quiz: Quiz, attempt: dict[str, object], results: list[dict[str, object]], score: int, correct: int, total: int, passed: bool) -> int | None:
    if attempt.get("submission_id"):
        return int(attempt["submission_id"])
    trainee = attempt.get("trainee", {})
    with db_connection() as db:
        cursor = db.execute(
            """
            INSERT INTO submissions (
                quiz_slug, quiz_title, trainee_name, callsign, score, correct_count,
                total_count, passing_score, passed, responses_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quiz.slug,
                quiz.title,
                str(trainee.get("name", "")),
                str(trainee.get("callsign", "")),
                score,
                correct,
                total,
                quiz.passing_score,
                1 if passed else 0,
                json.dumps(results),
                now_text(),
            ),
        )
        submission_id = int(cursor.lastrowid)
    attempt["submission_id"] = submission_id
    session[attempt_key(quiz.slug)] = attempt
    session.modified = True
    return submission_id


def reviewed_submission_score(responses: list[dict[str, object]], passing_score: int | None) -> dict[str, int | bool]:
    correct = 0
    total = 0
    for response in responses:
        if response.get("review_only"):
            review_status = response.get("review_status", "pending")
            if review_status == "pending":
                continue
            total += 1
            correct += int(review_status == "correct")
            continue
        total += 1
        correct += int(response.get("is_correct"))
    score = round((correct / total) * 100) if total else 0
    passed = passing_score is not None and score >= passing_score
    return {"correct": correct, "total": total, "score": score, "passed": passed}


@app.get("/")
def index() -> str:
    return render_template("home.html")


@app.get("/staff")
def staff_page() -> str:
    return render_template("staff.html", staff_sections=STAFF_SECTIONS)


@app.get("/employee/roster")
@login_required
def employee_roster_page() -> str:
    if request.args.get("embed") != "1":
        return redirect(f"{url_for('hub')}#panel-department-roster")
    roster_members, roster_is_live = official_doc_roster_members()
    section_titles = ("Command & Supervisors", "Officers & Cadets")
    roster_sections = []
    for index, title in enumerate(section_titles):
        members = [member for member in roster_members if member.get("section", "0") == str(index)]
        if members:
            roster_sections.append({"title": title, "members": members})
    return render_template(
        "mdt_roster.html",
        roster_sections=roster_sections,
        roster_members=roster_members,
        roster_is_live=roster_is_live,
        roster_sync=roster_sync_metadata(_DOC_ROSTER_CACHE),
    )


@app.get("/employee/profile")
@login_required
def employee_profile() -> str:
    user = current_user()
    assert user is not None
    roster_members, roster_available = official_doc_roster_members()
    member = roster_member_for_account(user, roster_members)
    return render_template(
        "employee_profile.html",
        profile=employee_profile_context(user, member, roster_available),
        embedded=request.args.get("embed") == "1",
    )


@app.get("/employee/profile/<callsign>")
@login_required
def employee_profile_by_callsign(callsign: str) -> str:
    roster_members, roster_available = official_doc_roster_members()
    identity = normalized_roster_identity(callsign)
    member = next(
        (item for item in roster_members if normalized_roster_identity(item.get("callsign")) == identity),
        None,
    )
    if member is None:
        abort(404)
    return render_template(
        "employee_profile.html",
        profile=employee_profile_context(account_for_roster_member(member), member, roster_available),
        embedded=request.args.get("embed") == "1",
    )


@app.post("/rosters/<roster_name>/refresh")
@login_required
def refresh_live_roster(roster_name: str) -> str:
    user = current_user()
    assert user is not None
    roster_sources = {
        "department": (_DOC_ROSTER_CACHE, official_doc_roster_members, lambda: True),
        "ftp": (_FTP_ROSTER_CACHE, ftp_roster_members, lambda: user_can_access_ftp(user)),
        "ert": (_ERT_ROSTER_CACHE, ert_roster_members, lambda: user_can_access_ert(user)),
    }
    source = roster_sources.get(roster_name)
    if source is None or not source[2]():
        abort(403)
    cache, loader, _ = source
    cache["fetched_at"] = 0.0
    members, available = loader()
    return jsonify({"ok": True, "available": available, "count": len(members), "sync": roster_sync_metadata(cache)})


@app.get("/divisions")
def divisions_page() -> str:
    return render_template("divisions.html", divisions=DIVISIONS)


@app.get("/hiring")
def hiring_page() -> str:
    return render_template("hiring.html")


@app.get("/quiz")
def quiz_home() -> str:
    identity_values = account_identity_values()
    return render_template(
        "index.html",
        quizzes=quiz_cards(CERT_QUIZZES),
        selected_quiz=None,
        ready_to_start=False,
        requires_identity=False,
        identity_values=identity_values,
        readonly_fields=set(identity_values),
        current_question=None,
        progress=None,
        results=None,
    )


@app.get("/redirect-missing/<target>")
def missing_redirect(target: str) -> str:
    flash(f"{target.title()} redirect is not configured yet.", "error")
    return redirect(url_for("index"))


@app.get("/complaints")
def complaints_page() -> str:
    return render_template("complaints.html")


@app.post("/complaints")
def submit_complaint() -> str:
    fields = {
        "discord_username": request.form.get("discord_username", "").strip(),
        "name": request.form.get("name", "").strip(),
        "contact_info": request.form.get("contact_info", "").strip(),
        "officer_reported": request.form.get("officer_reported", "").strip(),
        "topic_datetime": request.form.get("topic_datetime", "").strip(),
        "event_details": request.form.get("event_details", "").strip(),
        "evidence": request.form.get("evidence", "").strip(),
        "witnesses": request.form.get("witnesses", "").strip(),
        "additional_info": request.form.get("additional_info", "").strip(),
    }
    required = ("discord_username", "name", "contact_info", "officer_reported", "topic_datetime", "event_details")
    if any(not fields[key] for key in required):
        flash("Please fill out all required complaint fields.", "error")
        return redirect(url_for("complaints_page"))
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO complaints (
                discord_username, name, contact_info, officer_reported, topic_datetime,
                event_details, evidence, witnesses, additional_info, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["discord_username"],
                fields["name"],
                fields["contact_info"],
                fields["officer_reported"],
                fields["topic_datetime"],
                fields["event_details"],
                fields["evidence"],
                fields["witnesses"],
                fields["additional_info"],
                now_text(),
            ),
        )
    flash("Complaint submitted.", "success")
    return redirect(url_for("complaints_page"))


@app.get("/applications")
def applications_page() -> str:
    return render_template("applications.html", applications=APPLICATION_FORMS)


@app.get("/applications/<application_type>")
def application_form(application_type: str) -> str:
    form_config = APPLICATION_FORMS.get(application_type)
    if form_config is None:
        abort(404)
    user = current_user()
    identity_values = account_identity_values(user)
    return render_template(
        "application_form.html",
        application_type=application_type,
        form_config=form_config,
        identity_values=identity_values,
        readonly_fields=set(identity_values),
        embedded=request.args.get("embed") == "1",
    )


@app.post("/applications/<application_type>")
def submit_application(application_type: str) -> str:
    form_config = APPLICATION_FORMS.get(application_type)
    if form_config is None:
        abort(404)

    responses: list[dict[str, str]] = []
    missing_required = False
    field_values: dict[str, str] = {}
    identity_values = account_identity_values()
    for field in form_config["fields"]:
        name = str(field["name"])
        value = identity_values.get(name, request.form.get(name, "").strip())
        field_values[name] = value
        if field.get("required") and not value:
            missing_required = True
        responses.append({"label": str(field["label"]), "answer": value})

    if missing_required:
        flash("Please fill out all required application fields.", "error")
        return redirect(url_for("application_form", application_type=application_type, embed="1" if request.args.get("embed") == "1" else None))

    with db_connection() as db:
        db.execute(
            """
            INSERT INTO applications (
                application_type, discord_username, full_name, callsign, rank, responses_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_type,
                field_values.get("discord", ""),
                field_values.get("full_name", ""),
                field_values.get("callsign", ""),
                field_values.get("rank", ""),
                json.dumps(responses),
                now_text(),
            ),
        )
    flash("Application submitted.", "success")
    return redirect(url_for("applications_page", embed="1" if request.args.get("embed") == "1" else None))


@app.route("/supervisor/login", methods=["GET", "POST"])
def supervisor_login() -> str:
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = row_one("SELECT * FROM supervisor_users WHERE lower(username) = lower(?)", (username,))
        if user and check_password_hash(user["password_hash"], password):
            session["supervisor_user_id"] = user["id"]
            session["supervisor_username"] = user["username"]
            session["supervisor_role"] = user["role"]
            session["auth_version"] = user["auth_version"]
            record_login_event(user)
            return redirect(url_for("hub"))
        flash("Invalid username or password.", "error")
    return render_template("supervisor_login.html")


@app.post("/supervisor/logout")
def supervisor_logout() -> str:
    session.pop("supervisor_user_id", None)
    session.pop("supervisor_username", None)
    session.pop("supervisor_role", None)
    return redirect(url_for("index"))


@app.get("/hub")
@login_required
def hub() -> str:
    user = current_user()
    assert user is not None
    announcements = dashboard_announcements()
    events = dashboard_events()
    return render_template(
        "hub.html",
        current_user=user,
        announcements=announcements,
        events=events,
        calendar_view=dashboard_calendar(events, request.args.get("calendar_month", ""), request.args.get("timezone", "")),
        dashboard_stats=hub_dashboard_stats(announcements, events),
        notifications=hub_notifications(user, announcements),
        can_manage_dashboard=user_can_manage_dashboard(user),
        ert_division_ranks=ERT_DIVISION_RANKS,
        ftp_division_ranks=FTP_DIVISION_RANKS,
    )


@app.post("/hub/notifications/dismiss")
@login_required
def dismiss_hub_notification() -> str:
    user = current_user()
    assert user is not None
    notification_key = request.form.get("notification_key", "").strip()
    persistent_id = request.form.get("persistent_id", "").strip()
    timezone_name = request.form.get("timezone", "").strip()
    if persistent_id.isdigit():
        with db_connection() as db:
            db.execute("DELETE FROM user_notifications WHERE id = ? AND user_id = ?", (int(persistent_id), user["id"]))
    elif notification_key:
        with db_connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO notification_dismissals (user_id, notification_key, created_at) VALUES (?, ?, ?)",
                (user["id"], notification_key, now_text()),
            )
    return redirect(url_for("hub", timezone=timezone_name or None))


@app.post("/hub/announcements/add")
@login_required
def add_dashboard_announcement() -> str:
    user = current_user()
    assert user is not None
    if not user_can_manage_dashboard(user):
        abort(403)
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    priority = request.form.get("priority", "normal").strip()
    if priority not in {"normal", "urgent", "training"}:
        priority = "normal"
    if not title or not body:
        flash("Announcement title and body are required.", "error")
        return redirect(url_for("hub", _anchor="announcements"))
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO dashboard_announcements (
                author_user_id, author_name, author_callsign, title, body, priority, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                (user["display_name"] or user["username"]).strip(),
                (user["callsign"] or "").strip(),
                title,
                body,
                priority,
                now_text(),
            ),
        )
    flash("Announcement posted.", "success")
    return redirect(url_for("hub", _anchor="announcements"))


@app.post("/hub/events/add")
@login_required
def add_dashboard_event() -> str:
    user = current_user()
    assert user is not None
    if not user_can_manage_dashboard(user):
        abort(403)
    title = request.form.get("title", "").strip()
    event_date = request.form.get("event_date", "").strip()
    event_time = request.form.get("event_time", "").strip()
    event_type = request.form.get("event_type", "General").strip() or "General"
    notes = request.form.get("notes", "").strip()
    calendar_month = request.form.get("calendar_month", "").strip()
    event_timezone_name = request.form.get("event_timezone", "").strip()
    viewer_timezone_name, event_timezone = calendar_timezone(event_timezone_name)
    try:
        datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        flash("Choose a valid event date.", "error")
        return redirect(url_for("hub", _anchor="calendar"))
    if not title:
        flash("Event title is required.", "error")
        return redirect(url_for("hub", _anchor="calendar"))
    if event_type not in {"General", "Training"}:
        flash("Choose General or Training for the event type.", "error")
        return redirect(url_for("hub", calendar_month=calendar_month or None, _anchor="calendar"))
    if event_time:
        try:
            datetime.strptime(event_time, "%H:%M")
        except ValueError:
            flash("Choose a valid event time.", "error")
            return redirect(url_for("hub", calendar_month=calendar_month or None, _anchor="calendar"))
    event_start_utc = None
    if event_time:
        local_start = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M").replace(tzinfo=event_timezone)
        event_start_utc = local_start.astimezone(ZoneInfo("UTC")).isoformat()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO dashboard_events (
                creator_user_id, creator_name, creator_callsign, title,
                event_date, event_time, event_start_utc, event_timezone, event_type, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                (user["display_name"] or user["username"]).strip(),
                (user["callsign"] or "").strip(),
                title,
                event_date,
                event_time,
                event_start_utc,
                viewer_timezone_name,
                event_type,
                notes,
                now_text(),
            ),
        )
    flash("Calendar event added.", "success")
    local_event_month = event_date[:7]
    return redirect(url_for("hub", calendar_month=local_event_month, timezone=viewer_timezone_name, _anchor="calendar"))


@app.post("/hub/events/<int:event_id>/delete")
@login_required
def delete_dashboard_event(event_id: int) -> str:
    user = current_user()
    assert user is not None
    if not user_can_manage_dashboard(user):
        abort(403)
    calendar_month = request.form.get("calendar_month", "").strip()
    timezone_name = request.form.get("timezone", "").strip()
    with db_connection() as db:
        cursor = db.execute("DELETE FROM dashboard_events WHERE id = ?", (event_id,))
    flash("Calendar event deleted." if cursor.rowcount else "Calendar event not found.", "success" if cursor.rowcount else "error")
    return redirect(url_for("hub", calendar_month=calendar_month or None, timezone=timezone_name or None, _anchor="calendar"))


@app.get("/supervisor")
@supervisor_required
def supervisor_panel() -> str:
    user = current_user()
    return render_template(
        "supervisor_panel.html",
        rows=supervisor_quiz_rows(),
        test_requests=supervisor_test_requests(),
        submissions=supervisor_submissions(),
        cadet_timestamps=all_cadet_timestamps(),
        ftp_timestamp_followups=supervisor_ftp_timestamp_followups(),
        fto_evaluations=supervisor_fto_evaluations(),
        current_user=user,
        request_payload=request_payload,
    )


@app.get("/admin")
@admin_required
def admin_panel() -> str:
    user = current_user()
    return render_template(
        "admin_panel.html",
        answer_key_groups=builtin_answer_key_groups(),
        custom_questions=supervisor_custom_questions(),
        complaints=supervisor_complaints(),
        quiz_options=quiz_options(),
        current_user=user,
        supervisor_users=supervisor_users(),
        supervisor_users_by_role=supervisor_users_by_role(),
        user_roles=USER_ROLES,
        division_ranks=DIVISION_RANKS,
        division_rank_labels=DIVISION_RANK_LABELS,
        division_role_groups=DIVISION_ROLE_GROUPS,
    )


@app.get("/dev")
@owner_required
def dev_panel() -> str:
    user = current_user()
    return render_template("dev_panel.html", current_user=user, dev=dev_panel_data())


@app.get("/dev/health.json")
@owner_required
def dev_health_json() -> str:
    dev = dev_panel_data()
    return jsonify(
        {
            "runtime": dev["runtime"],
            "database": dev["database"],
            "git": dev["git"],
            "warnings": dev["warnings"],
            "assets": dev["assets"],
        }
    )


@app.get("/dev/routes.json")
@owner_required
def dev_routes_json() -> str:
    return jsonify({"routes": dev_panel_data()["routes"]})


@app.get("/dev/schema.json")
@owner_required
def dev_schema_json() -> str:
    return jsonify({"tables": dev_panel_data()["table_schema"]})


def admin_redirect() -> str:
    tab = request.form.get("active_tab", "")
    anchor = tab if tab in ADMIN_TAB_IDS else None
    return redirect(url_for("admin_panel", _anchor=anchor))


@app.get("/employee")
@employee_required
def employee_hub() -> str:
    user = current_user()
    return render_template("employee_hub.html", current_user=user)


@app.get("/division")
@app.get("/division/applications")
@division_applications_required
def division_command_panel() -> str:
    return redirect(url_for("hub"))


@app.get("/supervisor/submissions/<int:submission_id>")
@supervisor_required
def supervisor_submission_detail(submission_id: int) -> str:
    submission = row_one("SELECT * FROM submissions WHERE id = ?", (submission_id,))
    if submission is None:
        abort(404)
    responses = json.loads(submission["responses_json"])
    missed = [response for response in responses if not response.get("review_only") and not response["is_correct"]]
    review_only = [response for response in responses if response.get("review_only")]
    pending_review = [response for response in review_only if response.get("review_status", "pending") == "pending"]
    return render_template(
        "submission_detail.html",
        submission=submission,
        responses=responses,
        missed=missed,
        review_only=review_only,
        pending_review=pending_review,
    )


@app.get("/fto")
@fto_required
def fto_panel() -> str:
    user = current_user()
    assert user is not None
    cadets = active_cadets()
    archived = archived_cadets()
    all_timestamps = all_cadet_timestamps()
    ftp_members, ftp_roster_is_live = ftp_roster_members()
    if not ftp_roster_is_live:
        ftp_members = [
            {
                "name": (account["display_name"] or account["username"]).strip(),
                "badge": (account["callsign"] or "").strip(),
                "role": DIVISION_RANK_LABELS[user_division_rank_in(account, FTP_DIVISION_RANKS)].split(" · ")[-1],
                "status": "Account assignment",
                "timezone": "",
                "joined": "",
            }
            for account in supervisor_users()
            if user_has_division_rank(account, FTP_DIVISION_RANKS)
        ]
    return render_template(
        "fto_panel.html",
        cadets=cadets,
        ftp_members=ftp_members,
        ftp_roster_is_live=ftp_roster_is_live,
        ftp_roster_sync=roster_sync_metadata(_FTP_ROSTER_CACHE),
        can_archive_cadets=user["role"] in SUPERVISOR_ROLES or user_has_division_rank(user, {"ftp_supervisor", "ftp_command"}),
        archived_cadets=archived,
        cadet_timestamps=all_timestamps,
        cadet_logs_by_id={cadet["id"]: cadet_logs(cadet["id"]) for cadet in cadets},
        archived_logs_by_id={cadet["id"]: cadet_logs(cadet["id"]) for cadet in archived},
        cadet_timestamps_by_id=cadet_timestamps_by_cadet(cadets, all_timestamps),
        archived_timestamps=archived_cadet_timestamps(),
        identity_values=account_identity_values(),
        cadet_accounts=[account for account in supervisor_users() if account["role"] == "cadet"],
        fto_options=timestamp_fto_names(),
        training_results=supervisor_training_results(),
        training_quiz_results=supervisor_training_quiz_results(),
        can_review_ftp_applications=user_can_review_division_applications("ftp", user),
        ftp_applications=division_applications("ftp") if user_can_review_division_applications("ftp", user) else [],
        application_forms=APPLICATION_FORMS,
        now_text=now_text(),
    )


@app.get("/ert")
@ert_required
def ert_panel() -> str:
    user = current_user()
    assert user is not None
    members, roster_is_live = ert_roster_members()
    if not roster_is_live:
        members = [
            {
                "name": (account["display_name"] or account["username"]).strip(),
                "badge": (account["callsign"] or "").strip(),
                "role": DIVISION_RANK_LABELS[user_division_rank_in(account, ERT_DIVISION_RANKS)].split(" · ")[-1],
                "status": "Account assignment",
                "timezone": "",
                "joined": "",
            }
            for account in supervisor_users()
            if user_has_division_rank(account, ERT_DIVISION_RANKS)
        ]
    return render_template(
        "ert_panel.html",
        current_user=user,
        members=members,
        roster_is_live=roster_is_live,
        roster_sync=roster_sync_metadata(_ERT_ROSTER_CACHE),
        division_rank_labels=DIVISION_RANK_LABELS,
        training_logs=division_training_logs(),
        certification_logs=division_certification_logs(),
        certification_targets=certification_target_users(),
        training_departments=DIVISION_TRAINING_DEPARTMENTS,
        certifier_name=(user["display_name"] or user["username"]).strip(),
        certifier_callsign=(user["callsign"] or "").strip(),
        can_review_ert_applications=user_can_review_division_applications("ert", user),
        ert_applications=division_applications("ert") if user_can_review_division_applications("ert", user) else [],
        application_forms=APPLICATION_FORMS,
    )


@app.get("/cadet")
@cadet_panel_required
def cadet_panel() -> str:
    user = current_user()
    assert user is not None
    cadet_record = cadet_record_for_user(user)
    return render_template(
        "cadet_panel.html",
        current_user=user,
        cadet_record=cadet_record,
        can_open_fto_timestamps=user_can_access_ftp(user),
        fto_options=timestamp_fto_names(),
        timestamp_officers=cadet_timestamp_officers(user, cadet_record),
        active_timestamp=active_cadet_timestamp(user["id"]),
        timestamps=cadet_timestamp_logs(user["id"]),
    )


def supervisor_training_results() -> list[dict[str, object]]:
    results = []
    for row in rows_all("SELECT * FROM training_results ORDER BY id DESC LIMIT 100"):
        result = dict(row)
        try:
            result["responses"] = json.loads(row["responses_json"])
        except (TypeError, json.JSONDecodeError):
            result["responses"] = []
        results.append(result)
    return results


@app.get("/cadet/training")
@login_required
def cadet_training() -> str:
    user = current_user()
    assert user is not None
    identity_values = account_identity_values(user)
    return render_template(
        "cadet_training.html",
        current_user=user,
        training_categories=training_categories_with_overrides(),
        practice_quizzes=quiz_cards(TRAINING_QUIZZES),
        identity_values=identity_values,
        readonly_fields=set(identity_values),
        sop_source_url=SOP_SOURCE_URL,
    )


@app.post("/cadet/training/results")
@login_required
def save_cadet_training_result() -> str:
    user = current_user()
    assert user is not None
    payload = request.get_json(silent=True) or {}
    category_slug = str(payload.get("category_slug", "")).strip()
    category = next((item for item in CADET_TRAINING_CATEGORIES if item["slug"] == category_slug), None)
    responses = payload.get("responses")
    if category is None:
        return jsonify({"error": "Unknown training category."}), 400
    if not isinstance(responses, list):
        return jsonify({"error": "Training responses are required."}), 400
    total_count = len(category["questions"])
    multiple_choice_total = sum(1 for item in category["questions"] if item["type"] == "multiple_choice")
    correct_count = max(0, min(int(payload.get("correct_count", 0) or 0), multiple_choice_total))
    answered_count = max(0, min(int(payload.get("answered_count", 0) or 0), total_count))
    skipped_count = max(0, min(int(payload.get("skipped_count", 0) or 0), total_count))
    with db_connection() as db:
        cursor = db.execute(
            """
            INSERT INTO training_results (
                user_id, trainee_name, callsign, category_slug, category_title,
                correct_count, multiple_choice_total, answered_count, skipped_count,
                total_count, responses_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], (user["display_name"] or user["username"]).strip(),
                (user["callsign"] or "").strip(), category_slug, category["title"],
                correct_count, multiple_choice_total, answered_count, skipped_count,
                total_count, json.dumps(responses), now_text(),
            ),
        )
    return jsonify({"saved": True, "result_id": cursor.lastrowid})


@app.get("/cadet/timestamps/live")
@cadet_panel_required
def live_cadet_timestamps() -> str:
    user = current_user()
    assert user is not None
    return jsonify({"timestamps": [timestamp_payload(row) for row in cadet_timestamp_logs(user["id"])]})


@app.post("/cadet/fto-evaluations")
@cadet_panel_required
def submit_fto_evaluation() -> str:
    user = current_user()
    assert user is not None
    cadet_name = (user["display_name"] or user["username"]).strip()
    cadet_callsign = (user["callsign"] or "").strip()
    fto_name = request.form.get("fto_name", "").strip()
    teaching_rating = request.form.get("teaching_rating", "").strip()
    helpful_rating = request.form.get("helpful_rating", "").strip()
    overall_rating = request.form.get("overall_rating", "").strip()
    feedback = request.form.get("feedback", "").strip()

    def valid_rating(value: str, required: bool = True) -> int | None:
        if not value:
            return None if not required else -1
        try:
            rating = int(value)
        except ValueError:
            return -1
        return rating if 1 <= rating <= 10 else -1

    teaching_value = valid_rating(teaching_rating)
    helpful_value = valid_rating(helpful_rating)
    overall_value = valid_rating(overall_rating, required=False)
    if not cadet_callsign:
        flash("Your account needs a callsign before submitting an FTO evaluation.", "error")
        return redirect(url_for("cadet_panel"))
    if fto_name not in timestamp_fto_names() or teaching_value == -1 or helpful_value == -1 or overall_value == -1:
        flash("FTO, teaching rating, and helpfulness rating are required.", "error")
        return redirect(url_for("cadet_panel"))
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO fto_evaluations (
                cadet_user_id, cadet_name, cadet_callsign, fto_name,
                teaching_rating, helpful_rating, overall_rating, feedback, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                cadet_name,
                cadet_callsign,
                fto_name,
                int(teaching_value),
                int(helpful_value),
                overall_value,
                feedback,
                now_text(),
            ),
        )
    flash("FTO evaluation submitted.", "success")
    return redirect(url_for("cadet_panel"))


@app.get("/fto/timestamps/live")
@fto_required
def live_fto_timestamps() -> str:
    return jsonify({"timestamps": [timestamp_payload(row) for row in all_cadet_timestamps()]})


@app.get("/fto/timestamps/archive/live")
@fto_required
def live_archived_fto_timestamps() -> str:
    return jsonify({"timestamps": [timestamp_payload(row) for row in archived_cadet_timestamps()]})


@app.get("/supervisor/timestamps/live")
@supervisor_required
def live_supervisor_timestamps() -> str:
    return jsonify({"timestamps": [timestamp_payload(row) for row in all_cadet_timestamps()]})


@app.post("/supervisor/timestamps/<int:timestamp_id>/operation-reminder")
@supervisor_required
def send_timestamp_operation_reminder(timestamp_id: int) -> str:
    timestamp = row_one("SELECT * FROM cadet_timestamps WHERE id = ?", (timestamp_id,))
    if timestamp is None:
        abort(404)
    if not timestamp["stop_utc"]:
        flash("End the timestamp before sending an operation reminder.", "error")
        return redirect(url_for("supervisor_panel", _anchor="timestamp-followups-tab"))
    if row_one("SELECT id FROM cadet_logs WHERE timestamp_id = ?", (timestamp_id,)):
        flash("This timestamp already has an operation report.", "error")
        return redirect(url_for("supervisor_panel", _anchor="timestamp-followups-tab"))
    assignee = timestamp_assigned_ftp_member(timestamp)
    matching_cadet = next((cadet for cadet in rows_all("SELECT id, badge_number, name FROM cadets") if timestamp_matches_cadet(cadet, timestamp)), None)
    if assignee is None or matching_cadet is None:
        flash("This timestamp is not linked to an FTP account and cadet record.", "error")
        return redirect(url_for("supervisor_panel", _anchor="timestamp-followups-tab"))
    existing = row_one(
        "SELECT id FROM user_notifications WHERE user_id = ? AND timestamp_id = ? AND notification_type = 'timestamp_operation_reminder' AND read_at IS NULL",
        (assignee["id"], timestamp_id),
    )
    if existing:
        flash("A reminder is already waiting for this FTP member.", "success")
        return redirect(url_for("supervisor_panel", _anchor="timestamp-followups-tab"))
    cadet_label = f"{bracket_callsign(timestamp['cadet_callsign'])} {timestamp['cadet_name']}".strip()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO user_notifications (user_id, timestamp_id, notification_type, title, body, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                assignee["id"],
                timestamp_id,
                "timestamp_operation_reminder",
                "Complete timestamp operation",
                f"Complete timestamp #{timestamp_id} for {cadet_label}. The session ran from {timestamp['start_utc']} to {timestamp['stop_utc']}.",
                now_text(),
            ),
        )
    flash(f"Operation reminder sent to {roster_timestamp_name(assignee)}.", "success")
    return redirect(url_for("supervisor_panel", _anchor="timestamp-followups-tab"))


@app.post("/supervisor/timestamps/<int:timestamp_id>/pause")
@supervisor_required
def pause_cadet_timestamp(timestamp_id: int) -> str:
    paused_utc = utc_now_iso()
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE cadet_timestamps
            SET paused_utc = ?
            WHERE id = ? AND stop_utc IS NULL AND paused_utc IS NULL
            """,
            (paused_utc, timestamp_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id, "paused_utc": paused_utc})
    flash("Timestamp paused." if cursor.rowcount else "Timestamp could not be paused.", "success" if cursor.rowcount else "error")
    return redirect(url_for("supervisor_panel"))


@app.post("/supervisor/timestamps/<int:timestamp_id>/resume")
@supervisor_required
def resume_cadet_timestamp(timestamp_id: int) -> str:
    resumed_utc = utc_now_iso()
    timestamp = row_one("SELECT id, paused_utc, paused_total_seconds FROM cadet_timestamps WHERE id = ?", (timestamp_id,))
    if timestamp is None or not timestamp["paused_utc"]:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "id": timestamp_id})
        flash("Timestamp could not be resumed.", "error")
        return redirect(url_for("supervisor_panel"))
    paused_total = int(timestamp["paused_total_seconds"] or 0) + paused_seconds_between(timestamp["paused_utc"], resumed_utc)
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE cadet_timestamps
            SET paused_utc = NULL, paused_total_seconds = ?
            WHERE id = ? AND stop_utc IS NULL
            """,
            (paused_total, timestamp_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id, "paused_total_seconds": paused_total})
    flash("Timestamp resumed." if cursor.rowcount else "Timestamp could not be resumed.", "success" if cursor.rowcount else "error")
    return redirect(url_for("supervisor_panel"))


@app.post("/supervisor/timestamps/<int:timestamp_id>/end")
@supervisor_required
def end_cadet_timestamp(timestamp_id: int) -> str:
    stop_utc = request.form.get("stop_utc", "").strip() or utc_now_iso()
    stop_timezone = request.form.get("stop_timezone", "").strip()
    timestamp = row_one(
        "SELECT id, paused_utc, paused_total_seconds FROM cadet_timestamps WHERE id = ? AND stop_utc IS NULL",
        (timestamp_id,),
    )
    if timestamp is None:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "id": timestamp_id})
        flash("Timestamp could not be ended.", "error")
        return redirect(url_for("supervisor_panel"))
    paused_total_seconds = int(timestamp["paused_total_seconds"] or 0) + paused_seconds_between(timestamp["paused_utc"], stop_utc)
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE cadet_timestamps
            SET stop_utc = ?, stop_timezone = ?, paused_utc = NULL, paused_total_seconds = ?
            WHERE id = ? AND stop_utc IS NULL
            """,
            (stop_utc, stop_timezone, paused_total_seconds, timestamp_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id, "stop_utc": stop_utc})
    flash("Timestamp ended." if cursor.rowcount else "Timestamp could not be ended.", "success" if cursor.rowcount else "error")
    return redirect(url_for("supervisor_panel"))


@app.post("/supervisor/timestamps/<int:timestamp_id>/edit")
@supervisor_required
def edit_cadet_timestamp(timestamp_id: int) -> str:
    start_utc = request.form.get("start_utc", "").strip() or request.form.get("start_time", "").strip()
    stop_utc = request.form.get("stop_utc", "").strip() or request.form.get("stop_time", "").strip()
    start_timezone = request.form.get("start_timezone", "").strip()
    stop_timezone = request.form.get("stop_timezone", "").strip()
    timestamp = row_one("SELECT id, stop_utc, paused_utc, paused_total_seconds FROM cadet_timestamps WHERE id = ?", (timestamp_id,))
    if timestamp is None or not timestamp["stop_utc"] or not start_utc or not stop_utc:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "id": timestamp_id})
        flash("End the timestamp before editing its times.", "error")
        return redirect(url_for("supervisor_panel"))
    start_at = parse_timestamp(start_utc)
    stop_at = parse_timestamp(stop_utc)
    if start_at is None or (stop_utc and (stop_at is None or stop_at < start_at)):
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "id": timestamp_id, "error": "invalid_time"})
        flash("Timestamp times are invalid.", "error")
        return redirect(url_for("supervisor_panel"))
    paused_total_seconds = int(timestamp["paused_total_seconds"] or 0)
    paused_utc = timestamp["paused_utc"]
    if stop_utc:
        paused_total_seconds += paused_seconds_between(paused_utc, stop_utc)
        paused_utc = None
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE cadet_timestamps
            SET start_utc = ?, start_timezone = ?, stop_utc = ?, stop_timezone = ?,
                paused_utc = ?, paused_total_seconds = ?
            WHERE id = ?
            """,
            (start_utc, start_timezone, stop_utc or None, stop_timezone, paused_utc, paused_total_seconds, timestamp_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id})
    flash("Timestamp updated." if cursor.rowcount else "Timestamp could not be updated.", "success" if cursor.rowcount else "error")
    return redirect(url_for("supervisor_panel"))


@app.post("/supervisor/timestamps/<int:timestamp_id>/adjust")
@supervisor_required
def adjust_cadet_timestamp(timestamp_id: int) -> str:
    try:
        minutes = int(request.form.get("minutes", "0"))
    except ValueError:
        minutes = 0
    if minutes == 0 or abs(minutes) > 1440:
        return jsonify({"ok": False, "id": timestamp_id, "error": "Enter an adjustment between -1440 and 1440 minutes."}), 400
    timestamp = row_one("SELECT * FROM cadet_timestamps WHERE id = ?", (timestamp_id,))
    if timestamp is None:
        return jsonify({"ok": False, "id": timestamp_id, "error": "Timestamp not found."}), 404
    start_at = parse_timestamp(timestamp["start_utc"])
    effective_end = parse_timestamp(timestamp["stop_utc"] or timestamp["paused_utc"] or utc_now_iso())
    paused_seconds = int(timestamp["paused_total_seconds"] or 0)
    if start_at is None or effective_end is None:
        return jsonify({"ok": False, "id": timestamp_id, "error": "Timestamp dates are invalid."}), 400
    adjusted_start = start_at - timedelta(minutes=minutes)
    maximum_start = effective_end - timedelta(seconds=paused_seconds)
    if adjusted_start > maximum_start:
        return jsonify({"ok": False, "id": timestamp_id, "error": "That adjustment would reduce the session below zero minutes."}), 400
    adjusted_start_utc = adjusted_start.isoformat()
    with db_connection() as db:
        db.execute("UPDATE cadet_timestamps SET start_utc = ? WHERE id = ?", (adjusted_start_utc, timestamp_id))
    updated = row_one(
        """
        SELECT cadet_timestamps.*, supervisor_users.username AS account_username
        FROM cadet_timestamps
        LEFT JOIN supervisor_users ON supervisor_users.id = cadet_timestamps.cadet_user_id
        WHERE cadet_timestamps.id = ?
        """,
        (timestamp_id,),
    )
    return jsonify({"ok": True, "id": timestamp_id, "adjusted_minutes": minutes, "timestamp": timestamp_payload(updated) if updated else None})


@app.post("/fto/timestamps/<int:timestamp_id>/archive")
@fto_required
def archive_cadet_timestamp(timestamp_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute(
            "UPDATE cadet_timestamps SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
            (now_text(), timestamp_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id})
    if cursor.rowcount:
        flash("Timestamp archived.", "success")
    else:
        flash("Timestamp not found or already archived.", "error")
    return redirect(url_for("fto_panel", _anchor="fto-timestamps-tab"))


@app.post("/fto/timestamps/<int:timestamp_id>/restore")
@fto_required
def restore_cadet_timestamp(timestamp_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute(
            "UPDATE cadet_timestamps SET archived_at = NULL WHERE id = ? AND archived_at IS NOT NULL",
            (timestamp_id,),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": timestamp_id})
    if cursor.rowcount:
        flash("Timestamp restored.", "success")
    else:
        flash("Timestamp not found or already active.", "error")
    return redirect(url_for("fto_panel"))


@app.post("/fto/cadets/<int:cadet_id>/timestamps/start")
@fto_required
def start_managed_cadet_timestamp(cadet_id: int) -> str:
    user = current_user()
    assert user is not None
    cadet = row_one("SELECT * FROM cadets WHERE id = ? AND status = 'Active'", (cadet_id,))
    if cadet is None:
        abort(404)
    if active_timestamp_for_managed_cadet(cadet_id):
        flash("Stop this cadet's active timestamp before starting another one.", "error")
        return redirect(url_for("fto_panel", _anchor=f"cadet-time-{cadet_id}"))
    fto_name = request.form.get("fto_name", "").strip()
    fto_options = timestamp_fto_names()
    if fto_name not in fto_options:
        flash("Choose an FTO for the timestamp.", "error")
        return redirect(url_for("fto_panel", _anchor=f"cadet-time-{cadet_id}"))
    creator_name = (user["display_name"] or user["username"]).strip()
    creator_callsign = (user["callsign"] or "").strip()
    start_timezone = request.form.get("start_timezone", "").strip()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO cadet_timestamps (
                cadet_user_id, cadet_name, cadet_callsign, creator_name, creator_callsign,
                fto_name, start_utc, start_timezone, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                cadet["name"],
                cadet["badge_number"],
                creator_name,
                creator_callsign,
                fto_name,
                utc_now_iso(),
                start_timezone,
                now_text(),
            ),
        )
    flash("Cadet timestamp started.", "success")
    return redirect(url_for("fto_panel", _anchor=f"cadet-time-{cadet_id}"))


@app.post("/fto/cadets/<int:cadet_id>/timestamps/stop")
@fto_required
def stop_managed_cadet_timestamp(cadet_id: int) -> str:
    timestamp = active_timestamp_for_managed_cadet(cadet_id)
    if timestamp is None:
        flash("No active timestamp found for this cadet.", "error")
        return redirect(url_for("fto_panel", _anchor=f"cadet-time-{cadet_id}"))
    stop_utc = utc_now_iso()
    stop_timezone = request.form.get("stop_timezone", "").strip()
    paused_total_seconds = int(timestamp["paused_total_seconds"] or 0) + paused_seconds_between(timestamp["paused_utc"], stop_utc)
    with db_connection() as db:
        db.execute(
            """
            UPDATE cadet_timestamps
            SET stop_utc = ?, stop_timezone = ?, paused_utc = NULL, paused_total_seconds = ?
            WHERE id = ? AND stop_utc IS NULL
            """,
            (stop_utc, stop_timezone, paused_total_seconds, timestamp["id"]),
        )
    flash("Cadet timestamp stopped.", "success")
    return redirect(url_for("fto_panel", _anchor=f"cadet-time-{cadet_id}"))


@app.post("/cadet/timestamps/start")
@cadet_panel_required
def start_cadet_timestamp() -> str:
    user = current_user()
    assert user is not None
    if active_cadet_timestamp(user["id"]):
        flash("Stop your active timestamp before starting another one.", "error")
        return redirect(url_for("cadet_panel", _anchor="cadet-self-time"))
    cadet_name = (user["display_name"] or user["username"]).strip()
    cadet_callsign = (user["callsign"] or "").strip()
    creator_name = cadet_name
    creator_callsign = cadet_callsign
    fto_name = request.form.get("fto_name", "").strip()
    start_utc = utc_now_iso()
    start_timezone = request.form.get("start_timezone", "").strip()
    if not cadet_callsign:
        flash("Your account needs a callsign before you can start a timestamp.", "error")
        return redirect(url_for("cadet_panel", _anchor="cadet-self-time"))
    if not cadet_name or fto_name not in cadet_timestamp_options(user, cadet_record_for_user(user)):
        flash("Select an officer from Admin Accounts.", "error")
        return redirect(url_for("cadet_panel", _anchor="cadet-self-time"))
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO cadet_timestamps (
                cadet_user_id, cadet_name, cadet_callsign, creator_name, creator_callsign,
                fto_name, start_utc, start_timezone, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                cadet_name,
                cadet_callsign,
                creator_name,
                creator_callsign,
                fto_name,
                start_utc,
                start_timezone,
                now_text(),
            ),
        )
    flash("Timestamp started.", "success")
    return redirect(url_for("cadet_panel", _anchor="cadet-self-time"))


@app.post("/cadet/timestamps/<int:timestamp_id>/stop")
@cadet_panel_required
def stop_cadet_timestamp(timestamp_id: int) -> str:
    user = current_user()
    assert user is not None
    stop_utc = utc_now_iso()
    stop_timezone = request.form.get("stop_timezone", "").strip()
    timestamp = row_one(
        "SELECT id, paused_utc, paused_total_seconds FROM cadet_timestamps WHERE id = ? AND cadet_user_id = ? AND stop_utc IS NULL",
        (timestamp_id, user["id"]),
    )
    paused_total_seconds = int(timestamp["paused_total_seconds"] or 0) if timestamp else 0
    paused_total_seconds += paused_seconds_between(timestamp["paused_utc"], stop_utc) if timestamp else 0
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE cadet_timestamps
            SET stop_utc = ?, stop_timezone = ?, paused_utc = NULL, paused_total_seconds = ?
            WHERE id = ? AND cadet_user_id = ? AND stop_utc IS NULL
            """,
            (stop_utc, stop_timezone, paused_total_seconds, timestamp_id, user["id"]),
        )
    if cursor.rowcount:
        flash("Timestamp stopped.", "success")
    else:
        flash("Timestamp was already stopped or could not be found.", "error")
    return redirect(url_for("cadet_panel", _anchor="cadet-self-time"))


@app.post("/fto/cadets/add")
@fto_required
def add_cadet() -> str:
    cadet_user_id = request.form.get("cadet_user_id", "").strip()
    account = row_one(
        "SELECT id, username, display_name, callsign, role FROM supervisor_users WHERE id = ? AND role = 'cadet'",
        (cadet_user_id,),
    ) if cadet_user_id.isdigit() else None
    if account is None:
        flash("Select a Cadet account from Admin Accounts.", "error")
        return redirect(url_for("fto_panel", _anchor="fto-active-tab"))
    badge_number = (account["callsign"] or "").strip()
    name = (account["display_name"] or account["username"]).strip()
    discord = request.form.get("discord", "").strip()
    region = request.form.get("region", "").strip()
    phase = "Onboarding"
    hire_date = request.form.get("hire_date", "").strip()
    shifts_text = request.form.get("shifts", "0").strip()
    if not badge_number or not name:
        flash("The selected Cadet account needs both a callsign and name in Admin Accounts.", "error")
        return redirect(url_for("fto_panel", _anchor="fto-active-tab"))
    existing_cadet = row_one(
        "SELECT id FROM cadets WHERE status = 'Active' AND (LOWER(badge_number) = LOWER(?) OR LOWER(name) = LOWER(?)) LIMIT 1",
        (badge_number, name),
    )
    if existing_cadet:
        flash("That Cadet account is already on the active roster.", "error")
        return redirect(url_for("fto_panel", _anchor="fto-active-tab"))
    try:
        shifts = int(shifts_text or 0)
    except ValueError:
        shifts = 0
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO cadets (badge_number, discord, name, region, phase, status, shifts, hire_date, created_at)
            VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?)
            """,
            (badge_number, discord, name, region, phase, shifts, hire_date, now_text()),
        )
    flash("Cadet added.", "success")
    return redirect(url_for("fto_panel", _anchor="fto-active-tab"))


@app.post("/fto/cadets/<int:cadet_id>/archive")
@supervisor_required
def archive_cadet(cadet_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("UPDATE cadets SET status = 'Archived' WHERE id = ? AND status = 'Active'", (cadet_id,))
    if cursor.rowcount:
        flash("Cadet archived.", "success")
    else:
        flash("Cadet not found or already archived.", "error")
    return redirect(url_for("fto_panel"))


@app.post("/fto/cadets/<int:cadet_id>/restore")
@fto_required
def restore_cadet(cadet_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("UPDATE cadets SET status = 'Active' WHERE id = ? AND status IN ('Archived', 'Removed')", (cadet_id,))
    if cursor.rowcount:
        flash("Cadet restored to active list.", "success")
    else:
        flash("Cadet not found or already active.", "error")
    return redirect(url_for("fto_panel", _anchor="fto-cadet-archive-tab"))


@app.post("/fto/cadets/<int:cadet_id>/progress")
@fto_required
def update_cadet_progress(cadet_id: int) -> str:
    cadet = row_one("SELECT id FROM cadets WHERE id = ?", (cadet_id,))
    if cadet is None:
        abort(404)
    progress_values = {column: 1 if column in request.form else 0 for column in CADET_PROGRESS_CHECKS}
    text_values = {column: request.form.get(column, "").strip() for column in CADET_PROGRESS_TEXT}
    phase = request.form.get("phase", "").strip()
    if phase not in {"Onboarding", "Phase 1", "Phase 2", "Solo"}:
        flash("Choose a valid cadet phase.", "error")
        return redirect(url_for("fto_panel"))
    assignments = ["phase = ?", *[f"{column} = ?" for column in (*CADET_PROGRESS_CHECKS, *CADET_PROGRESS_TEXT)]]
    values = [phase]
    values.extend(progress_values[column] for column in CADET_PROGRESS_CHECKS)
    values.extend(text_values[column] for column in CADET_PROGRESS_TEXT)
    values.append(cadet_id)
    with db_connection() as db:
        db.execute(f"UPDATE cadets SET {', '.join(assignments)} WHERE id = ?", values)
    flash("Cadet progress updated.", "success")
    return redirect(url_for("fto_panel"))


@app.post("/fto/cadets/<int:cadet_id>/phase")
@fto_required
def update_cadet_phase(cadet_id: int) -> str:
    phase = request.form.get("phase", "").strip()
    if phase not in {"Onboarding", "Phase 1", "Phase 2", "Solo"}:
        flash("Choose a valid cadet phase.", "error")
        return redirect(url_for("fto_panel"))
    with db_connection() as db:
        cursor = db.execute("UPDATE cadets SET phase = ? WHERE id = ?", (phase, cadet_id))
    if cursor.rowcount:
        flash("Cadet phase saved.", "success")
    else:
        flash("Cadet not found.", "error")
    return redirect(url_for("fto_panel"))


@app.get("/fto/cadets/<int:cadet_id>")
@fto_required
def cadet_detail(cadet_id: int) -> str:
    cadet = row_one("SELECT * FROM cadets WHERE id = ?", (cadet_id,))
    if cadet is None:
        abort(404)
    identity_values = account_identity_values()
    return render_template(
        "cadet_detail.html",
        cadet=cadet,
        logs=cadet_logs(cadet_id),
        now_text=now_text(),
        identity_values=identity_values,
        readonly_fields=set(identity_values),
    )


@app.post("/fto/cadets/<int:cadet_id>/logs/add")
@fto_required
def add_cadet_log(cadet_id: int) -> str:
    cadet = row_one("SELECT * FROM cadets WHERE id = ?", (cadet_id,))
    if cadet is None:
        abort(404)
    user = current_user()
    assert user is not None
    identity_values = account_identity_values()
    training_officer = identity_values.get("training_officer", request.form.get("training_officer", "").strip())
    officer_badge = identity_values.get("officer_badge", request.form.get("officer_badge", "").strip())
    training_started = request.form.get("training_started_utc", "").strip() or request.form.get("training_started", "").strip()
    training_ended = request.form.get("training_ended_utc", "").strip() or request.form.get("training_ended", "").strip()
    training_timezone = request.form.get("training_timezone", "").strip()
    timestamp_id = request.form.get("timestamp_id", "").strip()
    return_to = request.form.get("return_to", "").strip()
    if timestamp_id:
        timestamp = row_one(
            "SELECT * FROM cadet_timestamps WHERE id = ? AND stop_utc IS NOT NULL AND archived_at IS NULL",
            (timestamp_id,),
        ) if timestamp_id.isdigit() else None
        if timestamp is None or not timestamp_matches_cadet(cadet, timestamp):
            flash("Select a completed timestamp logged for this cadet.", "error")
            if return_to == "fto_panel":
                return redirect(url_for("fto_panel", _anchor=f"cadet-ops-{cadet_id}"))
            return redirect(url_for("cadet_detail", cadet_id=cadet_id))
        training_officer, officer_badge = split_timestamp_officer(timestamp["fto_name"])
        training_started = timestamp["start_utc"]
        training_ended = timestamp["stop_utc"]
        training_timezone = timestamp["start_timezone"] or timestamp["stop_timezone"] or training_timezone
    activities = request.form.get("activities", "").strip()
    notes = request.form.get("notes", "").strip()
    logger_status = request.form.get("logger_status", "Updated").strip()
    if not all([training_officer, officer_badge, training_started, training_ended, activities, notes]):
        flash("Training officer, badge, times, activities, and notes are required. If you are logged in, make sure your account has a callsign.", "error")
        if return_to == "fto_panel":
            return redirect(url_for("fto_panel", _anchor=f"cadet-ops-{cadet_id}"))
        return redirect(url_for("cadet_detail", cadet_id=cadet_id))
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO cadet_logs (
                cadet_id, timestamp_id, training_officer, officer_badge, training_started, training_ended,
                training_timezone, activities, notes, logger_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cadet_id,
                int(timestamp_id) if timestamp_id.isdigit() else None,
                training_officer,
                officer_badge,
                training_started,
                training_ended,
                training_timezone,
                activities,
                notes,
                logger_status,
                now_text(),
            ),
        )
        db.execute("UPDATE cadets SET shifts = shifts + 1 WHERE id = ?", (cadet_id,))
        if timestamp_id.isdigit():
            db.execute(
                "UPDATE user_notifications SET read_at = ? WHERE user_id = ? AND timestamp_id = ? AND notification_type = 'timestamp_operation_reminder' AND read_at IS NULL",
                (now_text(), user["id"], int(timestamp_id)),
            )
    flash("Cadet training log added.", "success")
    if return_to == "fto_panel":
        return redirect(url_for("fto_panel", _anchor=f"cadet-ops-{cadet_id}"))
    return redirect(url_for("cadet_detail", cadet_id=cadet_id))


@app.post("/fto/cadets/<int:cadet_id>/notes/add")
@fto_required
def add_cadet_note(cadet_id: int) -> str:
    cadet = row_one("SELECT * FROM cadets WHERE id = ?", (cadet_id,))
    if cadet is None:
        abort(404)
    identity_values = account_identity_values()
    training_officer = identity_values.get("training_officer", "").strip()
    officer_badge = identity_values.get("officer_badge", "").strip()
    note = request.form.get("note", "").strip()
    if not all([training_officer, officer_badge, note]):
        flash("A note and your account callsign are required.", "error")
        return redirect(url_for("fto_panel"))
    logged_at = utc_now_iso()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO cadet_logs (
                cadet_id, training_officer, officer_badge, training_started, training_ended,
                training_timezone, activities, notes, logger_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cadet_id,
                training_officer,
                officer_badge,
                logged_at,
                logged_at,
                "",
                "Cadet Note",
                note,
                "Note",
                now_text(),
            ),
        )
    flash("Cadet note added.", "success")
    return redirect(url_for("fto_panel"))


@app.post("/fto/cadets/<int:cadet_id>/logs/<int:log_id>/update")
@fto_required
def update_cadet_log(cadet_id: int, log_id: int) -> str:
    log = row_one("SELECT id FROM cadet_logs WHERE id = ? AND cadet_id = ?", (log_id, cadet_id))
    if log is None:
        abort(404)
    training_officer = request.form.get("training_officer", "").strip()
    officer_badge = request.form.get("officer_badge", "").strip()
    training_started = request.form.get("training_started_utc", "").strip() or request.form.get("training_started", "").strip()
    training_ended = request.form.get("training_ended_utc", "").strip() or request.form.get("training_ended", "").strip()
    training_timezone = request.form.get("training_timezone", "").strip()
    activities = request.form.get("activities", "").strip()
    notes = request.form.get("notes", "").strip()
    logger_status = request.form.get("logger_status", "Updated").strip()
    if not all([training_officer, officer_badge, training_started, training_ended, activities, notes]):
        flash("Training officer, badge, times, activities, and notes are required.", "error")
        return redirect(url_for("cadet_detail", cadet_id=cadet_id))
    with db_connection() as db:
        db.execute(
            """
            UPDATE cadet_logs
            SET training_officer = ?, officer_badge = ?, training_started = ?, training_ended = ?,
                training_timezone = ?, activities = ?, notes = ?, logger_status = ?
            WHERE id = ? AND cadet_id = ?
            """,
            (
                training_officer,
                officer_badge,
                training_started,
                training_ended,
                training_timezone,
                activities,
                notes,
                logger_status,
                log_id,
                cadet_id,
            ),
        )
    flash("Training log updated.", "success")
    return redirect(url_for("cadet_detail", cadet_id=cadet_id))


@app.post("/supervisor/submissions/<int:submission_id>/delete")
@supervisor_required
def delete_submission(submission_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    if cursor.rowcount:
        flash("Quiz attempt record deleted.", "success")
    else:
        flash("Quiz attempt record was already gone.", "error")
    return redirect(url_for("supervisor_panel"))


@app.post("/supervisor/complaints/<int:complaint_id>/delete")
@admin_required
def delete_complaint(complaint_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("DELETE FROM complaints WHERE id = ?", (complaint_id,))
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": complaint_id})
    if cursor.rowcount:
        flash("Complaint deleted.", "success")
    else:
        flash("Complaint not found.", "error")
    return admin_redirect()


@app.post("/supervisor/applications/<int:application_id>/delete")
@admin_required
def delete_application(application_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": bool(cursor.rowcount), "id": application_id})
    if cursor.rowcount:
        flash("Application deleted.", "success")
    else:
        flash("Application not found.", "error")
    return admin_redirect()


@app.post("/supervisor/applications/<int:application_id>/vote")
@login_required
def vote_application(application_id: int) -> str:
    user = current_user()
    assert user is not None
    vote = request.form.get("vote", "").strip().lower()
    reviewer_note = request.form.get("reviewer_note", "").strip()[:1000]
    if vote not in {"yes", "no", "abstain", "clear"}:
        abort(400)
    application = row_one("SELECT id, application_type, decision_status FROM applications WHERE id = ?", (application_id,))
    if application is None:
        abort(404)
    application_type = str(application["application_type"])
    if not user_can_review_division_applications(application_type, user):
        abort(403)
    if application["decision_status"] in {"accepted", "denied"}:
        if request.headers.get("Accept") == "application/json":
            updated = next(
                (application_payload(row) for row in division_applications(application_type) if int(row["id"]) == application_id),
                None,
            )
            return jsonify({"ok": False, "application": updated, "error": "finalized"})
        flash("Voting is closed for that application.", "error")
        return division_application_redirect(application_type)
    with db_connection() as db:
        if vote == "clear":
            cursor = db.execute(
                "DELETE FROM application_votes WHERE application_id = ? AND user_id = ?",
                (application_id, user["id"]),
            )
        else:
            cursor = db.execute(
                """
                INSERT INTO application_votes (application_id, user_id, vote, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(application_id, user_id)
                DO UPDATE SET vote = excluded.vote, updated_at = excluded.updated_at
                """,
                (application_id, user["id"], vote, now_text(), now_text()),
            )
        if cursor.rowcount:
            db.execute(
                "INSERT INTO application_review_events (application_id, user_id, event_type, decision, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (application_id, user["id"], "vote" if vote != "clear" else "vote-cleared", vote, reviewer_note, now_text()),
            )
    finalize_application_if_ready(application_id)
    if request.headers.get("Accept") == "application/json":
        updated = next(
            (application_payload(row) for row in division_applications(application_type) if int(row["id"]) == application_id),
            None,
        )
        return jsonify({"ok": bool(cursor.rowcount), "application": updated})
    flash("Application vote updated.", "success")
    return division_application_redirect(application_type)


@app.post("/supervisor/applications/<int:application_id>/override/<decision>")
@login_required
def override_application(application_id: int, decision: str) -> str:
    user = current_user()
    assert user is not None
    if decision not in {"accept", "deny"}:
        abort(404)
    status = "accepted" if decision == "accept" else "denied"
    reviewer_note = request.form.get("reviewer_note", "").strip()[:1000]
    application = row_one("SELECT id, application_type FROM applications WHERE id = ?", (application_id,))
    if application is None:
        abort(404)
    application_type = str(application["application_type"])
    if not user_can_override_division_application(application_type, user):
        abort(403)
    with db_connection() as db:
        cursor = db.execute(
            """
            UPDATE applications
            SET decision_status = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, now_text(), application_id),
        )
        if cursor.rowcount:
            db.execute(
                "INSERT INTO application_review_events (application_id, user_id, event_type, decision, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (application_id, user["id"], "command-override", status, reviewer_note, now_text()),
            )
    if request.headers.get("Accept") == "application/json":
        updated = next(
            (application_payload(row) for row in division_applications(application_type) if int(row["id"]) == application_id),
            None,
        )
        return jsonify({"ok": bool(cursor.rowcount), "application": updated})
    flash(f"Application {status} by command override.", "success")
    return division_application_redirect(application_type)


@app.post("/supervisor/requests/<int:request_id>/<decision>")
@supervisor_required
def review_test_request(request_id: int, decision: str) -> str:
    if decision not in {"accept", "deny"}:
        abort(404)
    expire_stale_test_requests()
    user = current_user()
    request_row = row_one("SELECT * FROM test_requests WHERE id = ?", (request_id,))
    if request_row is None:
        abort(404)
    if request_row["status"] != "pending":
        flash("That request was already reviewed.", "error")
        return redirect(url_for("supervisor_panel"))
    if test_request_seconds_remaining(request_row) <= 0:
        expire_stale_test_requests()
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "id": request_id, "status": "expired", "message": "Request expired."}), 409
        flash("That request expired after 5 minutes.", "error")
        return redirect(url_for("supervisor_panel"))
    status = "accepted" if decision == "accept" else "denied"
    with db_connection() as db:
        db.execute(
            """
            UPDATE test_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (status, now_text(), user["username"], request_id),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": True, "id": request_id, "status": status})
    flash(f"Test request {status}.", "success")
    return redirect(url_for("supervisor_panel"))


@app.get("/supervisor/requests/live")
@supervisor_required
def live_test_requests() -> str:
    return jsonify({"requests": [request_payload(row) for row in supervisor_test_requests()]})


@app.get("/supervisor/complaints/live")
@admin_required
def live_complaints() -> str:
    return jsonify({"complaints": [complaint_payload(row) for row in supervisor_complaints()]})


@app.get("/supervisor/applications/live")
@admin_required
def live_applications() -> str:
    return jsonify({"applications": [application_payload(row) for row in supervisor_applications()]})


@app.get("/division/applications/live")
@division_applications_required
def live_division_applications() -> str:
    return jsonify({"applications": [application_payload(row) for row in supervisor_applications()]})


@app.post("/division/training-logs/add")
@ert_required
def add_division_training_log() -> str:
    user = current_user()
    assert user is not None
    training_title = request.form.get("training_title", "").strip()
    training_datetime = request.form.get("training_datetime", "").strip()
    departments = [
        department
        for department in request.form.getlist("departments")
        if department in DIVISION_TRAINING_DEPARTMENTS
    ]
    attendees = request.form.get("attendees", "").strip()
    notes = request.form.get("notes", "").strip()
    if not training_title or not training_datetime or not departments or not notes:
        flash("Training title, date/time, at least one department, and notes are required.", "error")
        return redirect(url_for("ert_panel", _anchor="ert-training-tab"))
    trainer_name = (user["display_name"] or user["username"]).strip()
    trainer_callsign = (user["callsign"] or "").strip()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO division_training_logs (
                trainer_user_id, trainer_name, trainer_callsign, training_title,
                departments_json, training_datetime, attendees, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                trainer_name,
                trainer_callsign,
                training_title,
                json.dumps(departments),
                training_datetime,
                attendees,
                notes,
                now_text(),
            ),
        )
    flash("Division training log saved.", "success")
    return redirect(url_for("ert_panel", _anchor="ert-training-tab"))


@app.post("/division/certification-logs/add")
@ert_required
def add_division_certification_log() -> str:
    user = current_user()
    assert user is not None
    certified_user_id_text = request.form.get("certified_user_id", "").strip()
    certification_name = request.form.get("certification_name", "").strip()
    certification_datetime = request.form.get("certification_datetime", "").strip()
    notes = request.form.get("notes", "").strip()
    try:
        certified_user_id = int(certified_user_id_text)
    except ValueError:
        certified_user_id = 0
    certified_user = row_one(
        "SELECT id, username, display_name, callsign FROM supervisor_users WHERE id = ?",
        (certified_user_id,),
    )
    if certified_user is None or not certification_name or not certification_datetime:
        flash("Certified officer, certification, and date/time are required.", "error")
        return redirect(url_for("ert_panel", _anchor="ert-certifications-tab"))
    certified_name = (certified_user["display_name"] or certified_user["username"]).strip()
    certified_callsign = (certified_user["callsign"] or "").strip()
    certifier_name = (user["display_name"] or user["username"]).strip()
    certifier_callsign = (user["callsign"] or "").strip()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO division_certification_logs (
                certified_user_id, certified_name, certified_callsign,
                certifier_user_id, certifier_name, certifier_callsign,
                certification_name, certification_datetime, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                certified_user["id"],
                certified_name,
                certified_callsign,
                user["id"],
                certifier_name,
                certifier_callsign,
                certification_name,
                certification_datetime,
                notes,
                now_text(),
            ),
        )
    flash("Certification log saved.", "success")
    return redirect(url_for("ert_panel", _anchor="ert-certifications-tab"))


@app.post("/supervisor/submissions/<int:submission_id>/written/<int:response_number>/review")
@supervisor_required
def review_written_response(submission_id: int, response_number: int) -> str:
    status = request.form.get("status", "")
    if status not in {"correct", "incorrect", "pending"}:
        abort(400)
    submission = row_one("SELECT responses_json, passing_score FROM submissions WHERE id = ?", (submission_id,))
    if submission is None:
        abort(404)
    responses = json.loads(submission["responses_json"])
    updated = False
    for response in responses:
        if response.get("number") == response_number and response.get("review_only"):
            response["review_status"] = status
            if status == "pending":
                response.pop("reviewed_correct", None)
            else:
                response["reviewed_correct"] = status == "correct"
            updated = True
            break
    if not updated:
        abort(404)
    recalculated = reviewed_submission_score(responses, submission["passing_score"])
    with db_connection() as db:
        db.execute(
            """
            UPDATE submissions
            SET responses_json = ?, score = ?, correct_count = ?, total_count = ?, passed = ?
            WHERE id = ?
            """,
            (
                json.dumps(responses),
                recalculated["score"],
                recalculated["correct"],
                recalculated["total"],
                1 if recalculated["passed"] else 0,
                submission_id,
            ),
        )
    flash("Written answer review saved.", "success")
    return redirect(url_for("supervisor_submission_detail", submission_id=submission_id, embed=1 if request.form.get("embed") else None))


@app.post("/supervisor/questions/add")
@admin_required
def add_custom_question() -> str:
    quiz_slug = request.form.get("quiz_slug", "").strip()
    if quiz_slug not in quiz_lookup():
        abort(400)
    prompt = request.form.get("prompt", "").strip()
    answer = request.form.get("answer", "").strip()
    category = request.form.get("category", "Custom").strip() or "Custom"
    written = bool(request.form.get("written"))
    raw_choices = [line.strip() for line in request.form.get("choices", "").splitlines() if line.strip()]
    if not prompt or not answer:
        flash("Question and answer are required.", "error")
        return admin_redirect()
    choices = cleaned_choices(answer, "\n".join(raw_choices), written)
    if not written:
        if len(choices) < 2:
            flash("Multiple choice questions need at least one wrong choice.", "error")
            return admin_redirect()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO custom_questions (quiz_slug, prompt, answer, choices_json, category, written, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (quiz_slug, prompt, answer, json.dumps(choices), category, 1 if written else 0, now_text()),
        )
    flash("Custom question added.", "success")
    return admin_redirect()


@app.post("/supervisor/questions/builtin/<path:question_key>/update")
@admin_required
def update_builtin_question(question_key: str) -> str:
    prompt = request.form.get("prompt", "").strip()
    answer = request.form.get("answer", "").strip()
    category = request.form.get("category", "Custom").strip() or "Custom"
    written = bool(request.form.get("written"))
    if not prompt or not answer:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "error": "Question and answer are required."}), 400
        flash("Question and answer are required.", "error")
        return admin_redirect()
    choices = cleaned_choices(answer, request.form.get("choices", ""), written)
    if not written and len(choices) < 2:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "error": "Multiple choice questions need at least one wrong choice."}), 400
        flash("Multiple choice questions need at least one wrong choice.", "error")
        return admin_redirect()
    with db_connection() as db:
        db.execute(
            """
            INSERT INTO question_overrides (question_key, prompt, answer, choices_json, category, written, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_key) DO UPDATE SET
                prompt = excluded.prompt,
                answer = excluded.answer,
                choices_json = excluded.choices_json,
                category = excluded.category,
                written = excluded.written,
                updated_at = excluded.updated_at
            """,
            (question_key, prompt, answer, json.dumps(choices), category, 1 if written else 0, now_text()),
        )
    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": True, "question_key": question_key, "answer": answer, "choices": list(choices)})
    flash("Built-in question updated.", "success")
    return admin_redirect()


@app.post("/supervisor/questions/builtin/<path:question_key>/reset")
@admin_required
def reset_builtin_question(question_key: str) -> str:
    with db_connection() as db:
        db.execute("DELETE FROM question_overrides WHERE question_key = ?", (question_key,))
    flash("Built-in question reset.", "success")
    return admin_redirect()


@app.post("/supervisor/questions/<int:question_id>/delete")
@admin_required
def delete_custom_question(question_id: int) -> str:
    with db_connection() as db:
        db.execute("DELETE FROM custom_questions WHERE id = ?", (question_id,))
    flash("Custom question deleted.", "success")
    return admin_redirect()


@app.post("/admin/timestamp-ftos/add")
@admin_required
def add_timestamp_fto() -> str:
    name = request.form.get("name", "").strip()
    if not name:
        flash("FTO name is required.", "error")
        return admin_redirect()
    if name.casefold() in {option.casefold() for option in timestamp_fto_names()}:
        flash("That person is already available from the roster.", "error")
        return admin_redirect()
    try:
        with db_connection() as db:
            db.execute(
                "INSERT INTO timestamp_ftos (name, created_at) VALUES (?, ?)",
                (name, now_text()),
            )
    except sqlite3.IntegrityError:
        flash("That FTO is already in the timestamp list.", "error")
        return admin_redirect()
    flash("FTO added to timestamp dropdown.", "success")
    return admin_redirect()


@app.post("/admin/timestamp-ftos/<int:fto_id>/delete")
@admin_required
def delete_timestamp_fto(fto_id: int) -> str:
    with db_connection() as db:
        cursor = db.execute("DELETE FROM timestamp_ftos WHERE id = ?", (fto_id,))
    if cursor.rowcount:
        flash("FTO removed from timestamp dropdown.", "success")
    else:
        flash("FTO not found.", "error")
    return admin_redirect()


@app.post("/supervisor/users/add")
@admin_required
def add_supervisor_user() -> str:
    actor = current_user()
    assert actor is not None
    username = request.form.get("username", "").strip()
    display_name = request.form.get("display_name", "").strip()
    callsign = request.form.get("callsign", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "supervisor")
    if role not in USER_ROLES:
        abort(400)
    if role == "owner" and actor["role"] != "owner":
        flash("Only the owner can create another owner account.", "error")
        return admin_redirect()
    if not username or not display_name or not password:
        flash("CID, name, and password are required.", "error")
        return admin_redirect()
    try:
        with db_connection() as db:
            db.execute(
                """
                INSERT INTO supervisor_users (username, display_name, callsign, phone_number, password_hash, role, division_rank, additional_division_rank, created_at)
                VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
                """,
                (username, display_name, callsign, phone_number, generate_password_hash(password), role, now_text()),
            )
    except sqlite3.IntegrityError:
        flash("That CID or callsign number is already assigned.", "error")
        return admin_redirect()
    flash("Login created.", "success")
    return admin_redirect()


@app.post("/supervisor/users/<int:user_id>/update")
@admin_required
def update_supervisor_user(user_id: int) -> str:
    actor = current_user()
    assert actor is not None
    target = row_one("SELECT * FROM supervisor_users WHERE id = ?", (user_id,))
    if target is None:
        abort(404)
    display_name = request.form.get("display_name", "").strip()
    callsign = request.form.get("callsign", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    role = request.form.get("role", target["role"])
    ert_rank = request.form.get("ert_rank", user_division_rank_in(target, ERT_DIVISION_RANKS))
    ftp_rank = request.form.get("ftp_rank", user_division_rank_in(target, FTP_DIVISION_RANKS))
    division_assignments = normalized_division_assignments(ert_rank, ftp_rank)
    password = request.form.get("password", "")
    if role not in USER_ROLES:
        abort(400)
    if target["role"] == "owner" and actor["role"] != "owner":
        flash("Only the owner can update an owner account.", "error")
        return admin_redirect()
    if role == "owner" and actor["role"] != "owner":
        flash("Only the owner can promote an account to owner.", "error")
        return admin_redirect()
    if division_assignments is None:
        abort(400)
    if not display_name:
        flash("Name is required.", "error")
        return admin_redirect()
    if target["role"] == "owner" and role != "owner":
        flash("Owner role cannot be removed.", "error")
        return admin_redirect()
    try:
        with db_connection() as db:
            db.execute(
                "UPDATE supervisor_users SET display_name = ?, callsign = ?, phone_number = ?, role = ?, division_rank = ?, additional_division_rank = ? WHERE id = ?",
                (display_name, callsign, phone_number, role, *division_assignments, user_id),
            )
            if password:
                db.execute(
                    "UPDATE supervisor_users SET password_hash = ?, auth_version = auth_version + 1, setup_pending = 0 WHERE id = ?",
                    (generate_password_hash(password), user_id),
                )
    except sqlite3.IntegrityError:
        flash("That callsign number is already assigned.", "error")
        return admin_redirect()
    flash("Login updated.", "success")
    return admin_redirect()


@app.post("/supervisor/users/<int:user_id>/delete")
@admin_required
def delete_supervisor_user(user_id: int) -> str:
    target = row_one("SELECT * FROM supervisor_users WHERE id = ?", (user_id,))
    if target is None:
        abort(404)
    if target["role"] == "owner":
        flash("Owner account cannot be deleted.", "error")
        return admin_redirect()
    if target["id"] == session.get("supervisor_user_id"):
        flash("You cannot delete the account you are logged into.", "error")
        return admin_redirect()
    with db_connection() as db:
        db.execute("DELETE FROM supervisor_users WHERE id = ?", (user_id,))
    flash("Login deleted.", "success")
    return admin_redirect()


@app.get("/quiz/<slug>")
def quiz(slug: str) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None:
        abort(404)
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    session.pop(attempt_key(slug), None)
    identity_values = account_identity_values()
    return render_template(
        "index.html",
        quizzes=quiz_cards(),
        selected_quiz=selected_quiz,
        ready_to_start=True,
        requires_identity=True,
        identity_values=identity_values,
        readonly_fields=set(identity_values),
        current_question=None,
        progress={"current": 0, "total": question_total},
        results=None,
    )


@app.post("/quiz/<slug>/start")
def start_quiz(slug: str) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None:
        abort(404)
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    identity_values = account_identity_values()
    trainee_name = identity_values.get("trainee_name", request.form.get("trainee_name", "").strip())
    callsign = identity_values.get("callsign", request.form.get("callsign", "").strip())
    if not trainee_name or not callsign:
        flash("Name and callsign are required for this quiz. If you are logged in, make sure your account has a callsign.", "error")
        return redirect(url_for("quiz", slug=slug))
    if slug in CERT_QUIZZES:
        with db_connection() as db:
            cursor = db.execute(
                """
                INSERT INTO test_requests (quiz_slug, quiz_title, trainee_name, callsign, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (slug, selected_quiz.title, trainee_name, callsign, now_text()),
            )
            request_id = int(cursor.lastrowid)
        session[request_key(slug)] = request_id
        session.modified = True
        return redirect(url_for("test_request_status", slug=slug, request_id=request_id))
    attempt = new_attempt(slug, questions, question_total)
    attempt["trainee"] = {"name": trainee_name, "callsign": callsign}
    session[attempt_key(slug)] = attempt
    session.modified = True
    return redirect(url_for("quiz_step", slug=slug))


@app.get("/quiz/<slug>/request/<int:request_id>")
def test_request_status(slug: str, request_id: int) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None or slug not in CERT_QUIZZES:
        abort(404)
    expire_stale_test_requests()
    request_row = row_one("SELECT * FROM test_requests WHERE id = ? AND quiz_slug = ?", (request_id, slug))
    if request_row is None:
        abort(404)
    return render_template(
        "request_status.html",
        selected_quiz=selected_quiz,
        request_row=request_row,
        request_payload=request_payload,
        request_expiry_minutes=TEST_REQUEST_EXPIRY_MINUTES,
    )


@app.get("/quiz/<slug>/request/<int:request_id>/status")
def live_request_status(slug: str, request_id: int) -> str:
    if slug not in CERT_QUIZZES:
        abort(404)
    expire_stale_test_requests()
    request_row = row_one("SELECT * FROM test_requests WHERE id = ? AND quiz_slug = ?", (request_id, slug))
    if request_row is None:
        abort(404)
    return jsonify(request_payload(request_row))


@app.post("/quiz/<slug>/request/<int:request_id>/begin")
def begin_approved_quiz(slug: str, request_id: int) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None or slug not in CERT_QUIZZES:
        abort(404)
    expire_stale_test_requests()
    request_row = row_one("SELECT * FROM test_requests WHERE id = ? AND quiz_slug = ?", (request_id, slug))
    if request_row is None:
        abort(404)
    if request_row["status"] != "accepted":
        flash("A supervisor has not accepted this request yet.", "error")
        return redirect(url_for("test_request_status", slug=slug, request_id=request_id))
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    attempt = new_attempt(slug, questions, question_total)
    attempt["trainee"] = {"name": request_row["trainee_name"], "callsign": request_row["callsign"]}
    attempt["request_id"] = request_id
    session[attempt_key(slug)] = attempt
    session.modified = True
    with db_connection() as db:
        db.execute("UPDATE test_requests SET started_at = ? WHERE id = ? AND started_at IS NULL", (now_text(), request_id))
    return redirect(url_for("quiz_step", slug=slug))


@app.post("/quiz/<slug>/answer")
def answer_question(slug: str) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None:
        abort(404)
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    attempt = current_attempt(slug, questions, question_total, create_if_missing=False)
    if attempt is None:
        flash("Your quiz session is no longer active. Please start the quiz again.", "error")
        return redirect(url_for("quiz", slug=slug))
    position = int(attempt["position"])
    order = list(attempt["order"])

    if position >= len(order):
        return redirect(url_for("quiz_results", slug=slug))

    question_index = order[position]
    question = questions[question_index]
    selected = request.form.get("answer", "").strip()
    if not selected:
        flash("Choose or enter an answer before continuing.", "error")
        return redirect(url_for("quiz_step", slug=slug))
    responses = list(attempt.get("responses", []))
    responses.append({"question_index": question_index, "selected": selected})

    attempt["responses"] = responses
    attempt["position"] = position + 1
    session[attempt_key(slug)] = attempt
    session.modified = True

    if attempt["position"] >= len(order):
        return redirect(url_for("quiz_results", slug=slug))
    return redirect(url_for("quiz_step", slug=slug))


@app.get("/quiz/<slug>/step")
def quiz_step(slug: str) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None:
        abort(404)
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    attempt = current_attempt(slug, questions, question_total, create_if_missing=False)
    if attempt is None:
        flash("Your quiz session is no longer active. Please start the quiz again.", "error")
        return redirect(url_for("quiz", slug=slug))
    position = int(attempt["position"])
    order = list(attempt["order"])

    if position >= len(order):
        return redirect(url_for("quiz_results", slug=slug))

    question_index = order[position]
    return render_template(
        "quiz_focus.html",
        selected_quiz=selected_quiz,
        current_question=question_payload(question_index, questions[question_index]),
        progress={"current": position + 1, "total": len(order)},
        results=None,
    )


@app.get("/quiz/<slug>/results")
def quiz_results(slug: str) -> str:
    selected_quiz = quiz_lookup().get(slug)
    if selected_quiz is None:
        abort(404)
    questions = mark_written_questions(questions_for_quiz(selected_quiz), selected_quiz.difficulty)
    question_total = quiz_question_total(selected_quiz, questions)
    attempt = current_attempt(slug, questions, question_total, create_if_missing=False)
    if attempt is None:
        flash("Your quiz session is no longer active. Please start the quiz again.", "error")
        return redirect(url_for("quiz", slug=slug))
    responses = list(attempt.get("responses", []))
    results = []
    correct = 0
    graded_total = 0

    for number, response in enumerate(responses, start=1):
        question = questions[response["question_index"]]
        selected = response["selected"]
        review_only = question.written
        is_correct = True if review_only else answer_matches(selected, question)
        if not review_only:
            graded_total += 1
            correct += int(is_correct)
        results.append(
            {
                "number": number,
                "prompt": question.prompt,
                "selected": selected or "No answer selected",
                "answer": question.answer,
                "is_correct": is_correct,
                "review_only": review_only,
                "review_status": "pending" if review_only else "graded",
                "category": question.category,
            }
        )

    score = round((correct / graded_total) * 100) if graded_total else 0
    passed = selected_quiz.passing_score is not None and score >= selected_quiz.passing_score
    submission_id = save_submission(selected_quiz, attempt, results, score, correct, graded_total, passed)
    return render_template(
        "quiz_focus.html",
        selected_quiz=selected_quiz,
        current_question=None,
        progress=None,
        results={
            "correct": correct,
            "total": graded_total,
            "review_only_total": len(questions) - graded_total,
            "score": score,
            "passing_score": selected_quiz.passing_score,
            "passed": passed,
            "show_feedback": selected_quiz.slug not in CERT_QUIZZES,
            "submission_id": submission_id,
            "responses": results,
        },
    )

init_db()

from mdt import register_mdt
register_mdt(app, globals())


if __name__ == "__main__":
    app.run(debug=True)
