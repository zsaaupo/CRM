from django.db import transaction
from django.db.models import F
from django.db.models.functions import Trim, Upper
from rest_framework import serializers

from audit_log.service import bulk_create_with_audit
from common.csv_files import MAX_CSV_ERRORS, csv_response, read_csv_rows

from .attendance_policy import classify_attendance
from .models import Attendance, Employee
from .serializers import AttendanceSerializer


REQUIRED_COLUMNS = ('employee_code', 'work_date')
OPTIONAL_COLUMNS = ('status', 'check_in', 'check_out', 'notes')


def attendance_csv_template():
    return csv_response('attendance-import-template.csv', (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS), ())


def attendance_row_data(row, employee_ids):
    employee_id = employee_ids.get(row['employee_code'].upper())
    if employee_id is None:
        raise serializers.ValidationError({'employee_code': ['Enter the code of a current employee.']})
    data = {'employee': employee_id, 'work_date': row['work_date']}
    data.update({column: row[column] for column in OPTIONAL_COLUMNS if row.get(column)})
    serializer = AttendanceSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class AttendanceCSVImportSerializer(serializers.Serializer):
    file = serializers.FileField(write_only=True)

    def validate_file(self, upload):
        return read_csv_rows(upload, REQUIRED_COLUMNS, optional=OPTIONAL_COLUMNS, row_label='attendance')

    @transaction.atomic
    def create(self, validated_data):
        rows = validated_data['file']
        actor = validated_data['actor'].get_username()
        codes = {row['employee_code'].upper() for _, row in rows if row['employee_code']}
        employees = Employee.objects.annotate(csv_code=Upper(Trim('employee_code'))).filter(csv_code__in=codes)
        # Use the same employee lock as manual attendance writes, including on SQLite.
        employees.update(updated_at=F('updated_at'))
        employee_ids = dict(employees.values_list('csv_code', 'pk'))
        records, errors, seen = [], [], {}
        for line, row in rows:
            try:
                data = attendance_row_data(row, employee_ids)
                key = (data['employee'].pk, data['work_date'])
                if key in seen:
                    raise serializers.ValidationError({
                        'work_date': [f'This employee and work date are already listed on row {seen[key]}.'],
                    })
            except serializers.ValidationError as exc:
                details = ' '.join(f'{field.replace("_", " ")}: {" ".join(messages)}'
                                   for field, messages in exc.detail.items())
                errors.append(f'Row {line}: {details}')
                if len(errors) == MAX_CSV_ERRORS:
                    break
            else:
                seen[key] = line
                records.append(Attendance(**data, create_by=actor, modified_by=actor))
        if errors:
            raise serializers.ValidationError({'file': errors})
        for record in records:
            classify_attendance(record)
        bulk_create_with_audit(Attendance.objects, records)
        return {'imported_count': len(records)}
