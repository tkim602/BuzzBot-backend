from datetime import date, time

from ingestion.probes.oscar import OscarMeetingSample, OscarSectionSample
from ingestion.schedule.normalize import normalize_sections


def test_normalize_sections_parses_timed_and_tba_meetings():
    meetings = (
        OscarMeetingSample(
            meeting_type="Class",
            time="3:30 pm - 4:45 pm",
            days="MW",
            location="Paper Tricentennial 109",
            date_range="Aug 24, 2026 - Dec 17, 2026",
            schedule_type="Lecture*",
            instructor="Kartik Goyal",
        ),
        OscarMeetingSample(
            meeting_type="Class",
            time="TBA",
            days="",
            location="TBA",
            date_range="Aug 24, 2026 - Dec 17, 2026",
            schedule_type="Lecture*",
            instructor="Kartik Goyal",
        ),
    )
    samples = [
        OscarSectionSample(
            title="Natural Language",
            crn="90427",
            subject="CS",
            course="7650",
            section="A",
            term_name="Fall 2026",
            campus="Georgia Tech-Atlanta * Campus",
            credits=3.0,
            meetings=meetings,
        )
    ]

    courses, sections, failures = normalize_sections("202608", samples)

    assert courses[0].course_number == "7650"
    assert sections[0].course_key == ("CS", "7650")
    assert sections[0].instructors == ("Kartik Goyal",)
    assert sections[0].schedule_type == "Lecture"
    meeting, tba = sections[0].meetings
    assert meeting.start_time == time(15, 30)
    assert meeting.end_time == time(16, 45)
    assert meeting.building == "Paper Tricentennial"
    assert meeting.room == "109"
    assert meeting.start_date == date(2026, 8, 24)
    assert meeting.end_date == date(2026, 12, 17)
    assert meeting.is_tba is False
    assert tba.is_tba is True
    assert tba.start_time is None
    assert tba.end_time is None
    assert tba.building is None
    assert tba.room is None
    assert failures == []
