"""
Herramientas de validación de calidad de datos.
"""

from datetime import UTC, datetime
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


async def validate_data_quality(
    data: dict[str, Any] | list,
    rules: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Validar la calidad de un dataset.

    Args:
        data: Datos a validar (diccionario o lista de registros)
        rules: Reglas de validación personalizadas

    Returns:
        Resultado de la validación
    """
    validation_result: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "valid": True,
        "score": 100.0,
        "checks": [],
        "errors": [],
        "warnings": [],
        "statistics": {}
    }

    # Convertir a lista si es necesario
    records = data if isinstance(data, list) else [data]

    if not records:
        validation_result["valid"] = False
        validation_result["errors"].append("Empty dataset")
        validation_result["score"] = 0
        return validation_result

    # Estadísticas básicas
    validation_result["statistics"]["total_records"] = len(records)

    # Check 1: Completitud
    completeness = _check_completeness(records)
    validation_result["checks"].append(completeness)
    if not completeness["passed"]:
        validation_result["warnings"].extend(completeness.get("issues", []))

    # Check 2: Consistencia de tipos
    consistency = _check_type_consistency(records)
    validation_result["checks"].append(consistency)
    if not consistency["passed"]:
        validation_result["errors"].extend(consistency.get("issues", []))
        validation_result["valid"] = False

    # Check 3: Valores duplicados
    duplicates = _check_duplicates(records)
    validation_result["checks"].append(duplicates)
    if not duplicates["passed"]:
        validation_result["warnings"].extend(duplicates.get("issues", []))

    # Check 4: Reglas personalizadas
    if rules:
        custom = _apply_custom_rules(records, rules)
        validation_result["checks"].append(custom)
        if not custom["passed"]:
            validation_result["errors"].extend(custom.get("issues", []))
            validation_result["valid"] = False

    # Calcular score
    passed_checks = sum(1 for c in validation_result["checks"] if c["passed"])
    total_checks = len(validation_result["checks"])
    validation_result["score"] = (passed_checks / total_checks * 100) if total_checks > 0 else 0

    logger.info(
        f"Data validation completed: score={validation_result['score']:.1f}%, "
        f"valid={validation_result['valid']}"
    )

    return validation_result


def _check_completeness(records: list[dict]) -> dict[str, Any]:
    """Verificar completitud de campos."""
    if not records:
        return {
            "name": "completeness",
            "passed": False,
            "issues": ["No records to check"]
        }

    # Obtener todos los campos
    all_fields: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            all_fields.update(record.keys())

    # Contar nulls por campo
    null_counts = dict.fromkeys(all_fields, 0)
    for record in records:
        if isinstance(record, dict):
            for field in all_fields:
                value = record.get(field)
                if value is None or value == "" or (isinstance(value, float) and str(value) == "nan"):
                    null_counts[field] += 1

    # Calcular porcentajes
    total = len(records)
    issues = []
    high_null_fields = []

    for field, nulls in null_counts.items():
        percentage = (nulls / total * 100) if total > 0 else 0
        if percentage > 50:
            high_null_fields.append(f"{field}: {percentage:.1f}% null")
        elif percentage > 20:
            issues.append(f"Field '{field}' has {percentage:.1f}% null values")

    if high_null_fields:
        issues.append(f"Critical null fields: {', '.join(high_null_fields)}")

    return {
        "name": "completeness",
        "passed": len(high_null_fields) == 0,
        "null_percentages": {
            field: (count / total * 100) if total > 0 else 0
            for field, count in null_counts.items()
        },
        "issues": issues
    }


def _check_type_consistency(records: list[dict]) -> dict[str, Any]:
    """Verificar consistencia de tipos de datos."""
    if not records:
        return {
            "name": "type_consistency",
            "passed": True,
            "issues": []
        }

    # Inferir tipos por campo
    field_types: dict[str, set] = {}

    for record in records:
        if isinstance(record, dict):
            for field, value in record.items():
                if value is not None:
                    value_type = type(value).__name__
                    if field not in field_types:
                        field_types[field] = set()
                    field_types[field].add(value_type)

    # Detectar inconsistencias
    issues = []
    for field, types in field_types.items():
        if len(types) > 1:
            # Permitir int/float como consistentes
            numeric_types = {"int", "float"}
            if not types.issubset(numeric_types):
                issues.append(
                    f"Field '{field}' has inconsistent types: {', '.join(types)}"
                )

    return {
        "name": "type_consistency",
        "passed": len(issues) == 0,
        "field_types": {field: list(types) for field, types in field_types.items()},
        "issues": issues
    }


def _check_duplicates(records: list[dict]) -> dict[str, Any]:
    """Verificar registros duplicados."""
    if not records:
        return {
            "name": "duplicates",
            "passed": True,
            "issues": []
        }

    # Convertir a strings para comparación
    record_strings = []
    for record in records:
        if isinstance(record, dict):
            # Ordenar keys para comparación consistente
            record_str = str(sorted(record.items()))
            record_strings.append(record_str)

    # Contar duplicados
    unique = set(record_strings)
    duplicate_count = len(record_strings) - len(unique)
    duplicate_percentage = (duplicate_count / len(record_strings) * 100) if record_strings else 0

    issues = []
    if duplicate_percentage > 10:
        issues.append(f"High duplicate rate: {duplicate_percentage:.1f}% ({duplicate_count} records)")
    elif duplicate_count > 0:
        issues.append(f"Found {duplicate_count} duplicate records ({duplicate_percentage:.1f}%)")

    return {
        "name": "duplicates",
        "passed": duplicate_percentage <= 10,
        "duplicate_count": duplicate_count,
        "duplicate_percentage": duplicate_percentage,
        "issues": issues
    }


def _apply_custom_rules(records: list[dict], rules: dict[str, Any]) -> dict[str, Any]:  # noqa: C901, PLR0912
    """Aplicar reglas de validación personalizadas."""
    issues = []

    # Regla: required_fields
    if "required_fields" in rules:
        for field in rules["required_fields"]:
            for i, record in enumerate(records):
                if isinstance(record, dict) and field not in record:
                    issues.append(f"Record {i}: missing required field '{field}'")

    # Regla: field_ranges
    if "field_ranges" in rules:
        for field, range_config in rules["field_ranges"].items():
            min_val = range_config.get("min")
            max_val = range_config.get("max")
            for i, record in enumerate(records):
                if isinstance(record, dict) and field in record:
                    value = record[field]
                    if isinstance(value, (int, float)):
                        if min_val is not None and value < min_val:
                            issues.append(
                                f"Record {i}: {field}={value} below minimum {min_val}"
                            )
                        if max_val is not None and value > max_val:
                            issues.append(
                                f"Record {i}: {field}={value} above maximum {max_val}"
                            )

    # Regla: allowed_values
    if "allowed_values" in rules:
        for field, allowed in rules["allowed_values"].items():
            for i, record in enumerate(records):
                if isinstance(record, dict) and field in record:
                    if record[field] not in allowed:
                        issues.append(
                            f"Record {i}: {field}='{record[field]}' not in allowed values"
                        )

    return {
        "name": "custom_rules",
        "passed": len(issues) == 0,
        "rules_applied": list(rules.keys()),
        "issues": issues[:20]  # Limitar issues para no saturar
    }


async def check_schema(
    data: dict[str, Any] | list,
    expected_schema: dict[str, str]
) -> dict[str, Any]:
    """
    Verificar que los datos coincidan con un esquema esperado.

    Args:
        data: Datos a verificar
        expected_schema: Esquema esperado {field_name: type_name}

    Returns:
        Resultado de la verificación
    """
    records = data if isinstance(data, list) else [data]

    if not records:
        return {
            "valid": False,
            "errors": ["No data to check"]
        }

    errors = []
    warnings = []

    # Verificar primer registro como muestra
    sample = records[0] if isinstance(records[0], dict) else {}

    # Campos faltantes
    for field, expected_type in expected_schema.items():
        if field not in sample:
            errors.append(f"Missing field: {field}")
        else:
            actual_type = type(sample[field]).__name__
            if not _types_compatible(actual_type, expected_type):
                errors.append(
                    f"Field '{field}': expected {expected_type}, got {actual_type}"
                )

    # Campos extra
    extra_fields = set(sample.keys()) - set(expected_schema.keys())
    if extra_fields:
        warnings.append(f"Extra fields not in schema: {', '.join(extra_fields)}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "fields_checked": list(expected_schema.keys()),
        "extra_fields": list(extra_fields)
    }


def _types_compatible(actual: str, expected: str) -> bool:
    """Verificar si dos tipos son compatibles."""
    # Normalizar nombres
    type_map = {
        "str": ["str", "string"],
        "int": ["int", "integer", "float", "number"],
        "float": ["float", "number", "int"],
        "bool": ["bool", "boolean"],
        "list": ["list", "array"],
        "dict": ["dict", "object"]
    }

    expected_lower = expected.lower()
    for base_type, aliases in type_map.items():
        if actual == base_type and expected_lower in aliases:
            return True

    return actual.lower() == expected_lower
