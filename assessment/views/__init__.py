"""Views package — re-exports all public view functions for URL routing."""
from .learner import (
    home,
    request_demo,
    start,
    attempt_question,
    attempt_submit,
    attempt_details,
    attempt_consent,
    attempt_instructions,
    attempt_submitted,
    session_join,
    session_consent,
    attempt_section_review_info,
    attempt_section_review_question,
    attempt_section_review_done,
)

from .marking import (
    assessor_mark_attempt,
    assessor_unlock_attempt,
    assessor_moderation,
    assessor_approve_moderation,
    assessor_archive,
    assessor_activity_report,
    assessor_activity_detail,
    assessor_auditor_reopen,
    assessor_auto_marked_attempt,
    assessor_new_attempt,
    assessor_review_queue,
    assessor_working_sheet_upload,
    assessor_working_sheet_image,
    assessor_working_sheet_print,
    assessor_writing_submission_upload,
    assessor_writing_submission_image,
    assessor_scoring_breakdown,
    assessor_score_audit_log,
)

from .assessor import (
    assessor_dashboard,
    assessor_print_queue_json,
    assessor_metrics,
    assessor_metrics_simulate,
    assessor_guide,
    assessor_attempts,
    assessor_results,
    assessor_results_export,
    assessor_sessions,
    assessor_new_session,
    session_monitor,
    assessor_questions,
    assessor_toggle_question,
)

from .errors import (
    csrf_failure,
    handler400,
    handler403,
    handler404,
    handler500,
    error_report,
    error_preview,
    dev_doc_view,
    dev_report_file,
    dev_reporting_dashboard,
)

from .auth import (
    set_active_role,
    register,
    generate_invite,
)
