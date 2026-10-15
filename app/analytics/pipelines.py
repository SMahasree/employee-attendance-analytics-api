from typing import Any, Optional

def employee_monthly_pipeline(emp_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
        {"$addFields": {"date_obj": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}}}},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$dayOfWeek": {"date": "$date_obj", "timezone": "Asia/Kolkata"}}, [2, 3, 4, 5, 6]]},
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]
                }
            },
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"},
        }},
    ]


def department_summary_pipeline(start_date: str, end_date: str, department: Optional[str]) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$addFields": {"date_obj": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}}}},
        {"$group": {
            "_id": "$employee.department",
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$dayOfWeek": {"date": "$date_obj", "timezone": "Asia/Kolkata"}}, [2, 3, 4, 5, 6]]},
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]
                }
            },
            "avg_work_hours": {"$avg": {"$cond": [{"$and": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ne": ["$work_hours", None]}]}, "$work_hours", None]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ])
    return pipeline


def late_leaderboard_pipeline(
    start_date: str,
    end_date: str,
    department: Optional[str],
    limit: int,
) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$employee.name"},
            "department": {"$first": "$employee.department"},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": 1,
            "department": 1,
            "total_late_minutes": 1,
            "late_count": 1,
            "rank": 1,
        }},
    ])
    return pipeline


def department_trend_pipeline(department: str, from_date: str, day_diff: int) -> list[dict[str, Any]]:
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "employees": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}}}},
        {"$project": {
            "_id": 0,
            "employees": 1,
            "dates": {"$map": {
                "input": {"$range": [0, day_diff]},
                "as": "offset",
                "in": {"$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": {"$dateAdd": {
                        "startDate": {"$dateFromString": {"dateString": from_date, "timezone": "Asia/Kolkata"}},
                        "unit": "day",
                        "amount": "$$offset",
                    }},
                    "timezone": "Asia/Kolkata",
                }},
            }},
        }},
        {"$unwind": "$dates"},
        {"$set": {
            "date": "$dates",
            "eligible_employees": {"$filter": {
                "input": "$employees",
                "as": "employee",
                "cond": {"$lte": ["$$employee.joined_on", "$dates"]},
            }},
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {
                "day": "$date",
                "employee_codes": {"$map": {
                    "input": "$eligible_employees",
                    "as": "employee",
                    "in": "$$employee.emp_code",
                }},
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$date", "$$day"]},
                    {"$in": ["$emp_code", "$$employee_codes"]},
                ]}}},
                {"$group": {
                    "_id": None,
                    "present_count": {"$sum": {"$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                }},
            ],
            "as": "daily",
        }},
        {"$set": {
            "headcount": {"$size": "$eligible_employees"},
            "present_count": {"$ifNull": [{"$arrayElemAt": ["$daily.present_count", 0]}, 0]},
            "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily.late_count", 0]}, 0]},
            "is_working_day": {"$in": [
                {"$dayOfWeek": {
                    "date": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}},
                    "timezone": "Asia/Kolkata",
                }},
                [2, 3, 4, 5, 6],
            ]},
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": [
                {"$floor": {"$add": [
                    {"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 1000000]},
                    0.5,
                ]}},
                10000,
            ]},
            None,
        ]}}},
        {"$sort": {"date": 1}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]},
            }},
        }},
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": {"$cond": [
                {"$ne": ["$moving_avg_7d", None]},
                {"$divide": [
                    {"$floor": {"$add": [{"$multiply": ["$moving_avg_7d", 10000]}, 0.5]}},
                    10000,
                ]},
                None,
            ]},
        }},
        {"$sort": {"date": 1}},
    ]
