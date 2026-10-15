"""Compose the per-endpoint routers into the application router."""

from fastapi import APIRouter

from .endpoints import (
    admin_explain,
    analytics_department_summary,
    analytics_department_trend,
    analytics_employee_monthly,
    analytics_late_leaderboard,
    attendance_list,
    attendance_punch_in,
    attendance_punch_out,
    attendance_regularize,
    employees_create,
    employees_list,
    health,
    root,
)

router = APIRouter()

for endpoint_module in (
    root,
    health,
    employees_create,
    employees_list,
    attendance_punch_in,
    attendance_punch_out,
    attendance_list,
    attendance_regularize,
    analytics_employee_monthly,
    analytics_department_summary,
    analytics_late_leaderboard,
    analytics_department_trend,
    admin_explain,
):
    router.include_router(endpoint_module.router)
