"""STEP/STP assembly -> one ZZX per supported hollow-tube definition.

The STEP reader preserves the assembly/product structure so repeated component
occurrences can be counted. Each unique component definition is exported to a
temporary IGES file and passed through the same geometry analyser/writer used
by the proven IGS -> ZZX path. This deliberately keeps STEP output semantically
identical to the existing IGS-generated ZZX files instead of introducing a
second ZZX geometry implementation.
"""

from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path
import re
import tempfile
import time

import numpy as np

from .igs_converter import (
    IgsConversionError,
    _analyse_iges,
    _simplify_closed_polyline,
    _write_zzx,
)


class StepConversionError(ValueError):
    pass


def _step_api():
    try:
        from OCP.IGESControl import IGESControl_Writer
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDF import TDF_Label, TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.IFSelect import IFSelect_RetDone
    except ImportError as exc:
        raise StepConversionError(
            "Modulo CAD OpenCascade STEP non disponibile. "
            "Installa le dipendenze aggiornate del programma."
        ) from exc

    return {
        "IGESControl_Writer": IGESControl_Writer,
        "STEPCAFControl_Reader": STEPCAFControl_Reader,
        "TCollection_ExtendedString": TCollection_ExtendedString,
        "TDataStd_Name": TDataStd_Name,
        "TDF_Label": TDF_Label,
        "TDF_LabelSequence": TDF_LabelSequence,
        "TDocStd_Document": TDocStd_Document,
        "XCAFDoc_DocumentTool": XCAFDoc_DocumentTool,
        "IFSelect_RetDone": IFSelect_RetDone,
    }


def _step_log(path, message):
    name = Path(path).name if path is not None else "?"
    print(
        f"[STP pid={os.getpid()}] [{name}] {message}",
        flush=True,
    )


def _label_name(label, api):
    attribute = api["TDataStd_Name"]()
    if label.FindAttribute(
        api["TDataStd_Name"].GetID_s(),
        attribute,
    ):
        value = attribute.Get().ToExtString()
        if value:
            return str(value).strip()
    return ""


def _label_key(label):
    tags = []
    current = label
    guard = 0
    while not current.IsRoot() and guard < 64:
        tags.append(int(current.Tag()))
        current = current.Father()
        guard += 1
    return tuple(reversed(tags))


def _sanitize_component_name(value):
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:100] or "componente"


def _format_mm(value, digits=3):
    value = float(value)
    rounded = round(value)
    if abs(value - rounded) <= 10 ** (-(digits + 1)):
        return str(int(rounded))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _tube_filename(component_name, model, quantity):
    component_name = _sanitize_component_name(component_name)
    kind = model["profile_kind"]
    width = float(model["outside_width"])
    height = float(model["outside_height"])
    thickness = float(model["thickness"])
    length = float(model["overall_length"])

    if kind == "Circle":
        profile = (
            f"tubo Ø{_format_mm(width)}x{_format_mm(thickness)}"
        )
    else:
        profile = (
            "tubolare "
            f"{_format_mm(width)}x{_format_mm(height)}"
            f"x{_format_mm(thickness)}"
        )

    return (
        f"{component_name} {profile} "
        f"L{_format_mm(length)} {int(quantity)}pz.zzx"
    )


GEOMETRY_SIGNATURE_QUANTUM_MM = 0.02


def _quantize_geometry_value(value):
    return int(
        round(
            float(value)
            / GEOMETRY_SIGNATURE_QUANTUM_MM
        )
    )


def _canonical_point_cloud(points, transform):
    points = _simplify_closed_polyline(
        np.asarray(points, dtype=float)
    )
    points = np.asarray(points[:-1], dtype=float)
    if not len(points):
        return ()

    transformed = np.asarray(
        [transform(point) for point in points],
        dtype=float,
    )
    quantized = np.rint(
        transformed / GEOMETRY_SIGNATURE_QUANTUM_MM
    ).astype(np.int64)

    # The same CAD contour can start at a different vertex or run in the
    # opposite direction. The ZZX geometry is unchanged, so compare its
    # quantized point set rather than its traversal order.
    unique_points = sorted(
        {
            tuple(int(value) for value in row)
            for row in quantized
        }
    )
    return tuple(unique_points)


def _transverse_rotation(point, quarter_turns):
    x, y, z = map(float, point)
    turns = int(quarter_turns) % 4
    if turns == 0:
        return np.array([x, y, z], dtype=float)
    if turns == 1:
        return np.array([-y, x, z], dtype=float)
    if turns == 2:
        return np.array([-x, -y, z], dtype=float)
    return np.array([y, -x, z], dtype=float)


def _geometry_transforms(model):
    kind = str(model.get("profile_kind") or "")
    width = float(model.get("outside_width") or 0.0)
    height = float(model.get("outside_height") or 0.0)
    length = float(model.get("overall_length") or 0.0)

    if kind == "Square" or abs(width - height) <= 0.05:
        quarter_turns = (0, 1, 2, 3)
    elif kind == "Rect":
        # The analyser normally aligns the local axes consistently for a
        # rectangle. Only the true 0/180-degree rectangle symmetries are
        # equivalent once long/short faces are established.
        quarter_turns = (0, 2)
    elif kind == "Circle":
        # STEP component definitions exported by the same CAD assembly retain
        # a stable local angular datum. 90-degree candidates also cover common
        # CAD re-orientations without collapsing genuinely different hole
        # patterns.
        quarter_turns = (0, 1, 2, 3)
    else:
        quarter_turns = (0,)

    transforms = []
    for turns in quarter_turns:
        def forward(point, turns=turns):
            return _transverse_rotation(point, turns)

        transforms.append(forward)

        # A duplicate STEP definition can be modelled from the opposite end.
        # Reverse Z with a 180-degree proper rotation about local X, then apply
        # the normal transverse symmetry. This treats the same physical tube
        # as identical without mirroring its machining geometry.
        def reversed_axis(point, turns=turns, length=length):
            x, y, z = map(float, point)
            reversed_point = np.array(
                [x, -y, length - z],
                dtype=float,
            )
            return _transverse_rotation(
                reversed_point,
                turns,
            )

        transforms.append(reversed_axis)

    return transforms


def _model_geometry_signature(model):
    """Canonical machining signature for merging duplicate STEP tubes.

    Length alone is deliberately insufficient. Every end cut and internal
    machining contour contributes its full 3D geometry, so two Lxxxx tubes
    with holes at different positions remain separate.
    """
    kind = str(model.get("profile_kind") or "")
    dimensions = sorted(
        [
            _quantize_geometry_value(
                model.get("outside_width") or 0.0
            ),
            _quantize_geometry_value(
                model.get("outside_height") or 0.0
            ),
        ]
    )
    profile_signature = (
        kind,
        tuple(dimensions),
        _quantize_geometry_value(
            model.get("thickness") or 0.0
        ),
        _quantize_geometry_value(
            model.get("corner_radius") or 0.0
        ),
        _quantize_geometry_value(
            model.get("overall_length") or 0.0
        ),
    )

    operations = list(model.get("operations") or [])
    candidates = []

    for transform in _geometry_transforms(model):
        operation_signatures = []
        for operation in operations:
            outer = _canonical_point_cloud(
                operation["outer"],
                transform,
            )
            inner = _canonical_point_cloud(
                operation["inner"],
                transform,
            )
            operation_signatures.append(
                (outer, inner)
            )

        candidates.append(
            tuple(sorted(operation_signatures))
        )

    return (
        profile_signature,
        min(candidates) if candidates else (),
    )


def _merged_component_name(names):
    unique_names = []
    seen = set()
    for value in names:
        name = _sanitize_component_name(value)
        folded = name.casefold()
        if folded not in seen:
            seen.add(folded)
            unique_names.append(name)

    if not unique_names:
        return "componente"
    if len(unique_names) == 1:
        return unique_names[0]

    # Inventor/STEP exports in the supplied assembly use names such as
    # TA9900A00080_oa_0, ..._oa_1 for separate definitions of an identical
    # physical tube. If geometry proves they are identical, use the shared
    # production code rather than an arbitrary occurrence suffix.
    bases = []
    for name in unique_names:
        match = re.match(
            r"^(.*?)[_ -]oa[_ -]?\d+$",
            name,
            flags=re.IGNORECASE,
        )
        if not match:
            return unique_names[0]
        bases.append(match.group(1).rstrip("_ -"))

    if (
        bases
        and all(
            base.casefold() == bases[0].casefold()
            for base in bases
        )
    ):
        return bases[0]

    return unique_names[0]


def _collect_step_definitions(source_path, api):
    document = api["TDocStd_Document"](
        api["TCollection_ExtendedString"]("ULTRA STEP import")
    )
    reader = api["STEPCAFControl_Reader"]()
    status = reader.ReadFile(str(source_path))
    if status != api["IFSelect_RetDone"]:
        raise StepConversionError(
            "OpenCascade non riesce a leggere questo file STP/STEP."
        )

    if not reader.Transfer(document):
        raise StepConversionError(
            "OpenCascade non riesce a trasferire la struttura STEP."
        )

    shape_tool = api["XCAFDoc_DocumentTool"].ShapeTool_s(
        document.Main()
    )
    roots = api["TDF_LabelSequence"]()
    shape_tool.GetFreeShapes(roots)

    if roots.Length() <= 0:
        raise StepConversionError(
            "Il file STEP non contiene forme esportabili."
        )

    definitions = OrderedDict()
    leaf_occurrences = 0

    def register_definition(label, fallback_name):
        nonlocal leaf_occurrences
        key = _label_key(label)
        name = (
            _label_name(label, api)
            or fallback_name
            or f"componente_{len(definitions) + 1:03d}"
        )
        shape = shape_tool.GetShape_s(label)
        if shape.IsNull():
            return

        leaf_occurrences += 1
        entry = definitions.get(key)
        if entry is None:
            definitions[key] = {
                "key": key,
                "name": name,
                "shape": shape,
                "occurrence_count": 1,
            }
        else:
            entry["occurrence_count"] += 1

    def visit_definition(label, fallback_name="", ancestry=()):
        key = _label_key(label)
        if key in ancestry:
            raise StepConversionError(
                "La struttura STEP contiene un riferimento ciclico."
            )

        if shape_tool.IsAssembly_s(label):
            components = api["TDF_LabelSequence"]()
            shape_tool.GetComponents_s(
                label,
                components,
                False,
            )
            next_ancestry = ancestry + (key,)
            for index in range(1, components.Length() + 1):
                component = components.Value(index)
                component_name = _label_name(component, api)
                referred = api["TDF_Label"]()
                if shape_tool.GetReferredShape_s(
                    component,
                    referred,
                ):
                    visit_definition(
                        referred,
                        component_name,
                        next_ancestry,
                    )
                elif shape_tool.IsShape_s(component):
                    register_definition(
                        component,
                        component_name,
                    )
            return

        register_definition(
            label,
            _label_name(label, api) or fallback_name,
        )

    for index in range(1, roots.Length() + 1):
        visit_definition(roots.Value(index))

    return {
        "definitions": list(definitions.values()),
        "occurrence_count": leaf_occurrences,
        "root_count": roots.Length(),
    }


def _export_shape_to_iges(shape, destination, api):
    writer = api["IGESControl_Writer"]("MM", 0)
    if not writer.AddShape(shape):
        raise StepConversionError(
            "OpenCascade non riesce a esportare il componente STEP "
            "verso la pipeline geometrica IGS."
        )
    if not writer.Write(str(destination)):
        raise StepConversionError(
            "Impossibile creare il file IGS temporaneo del componente STEP."
        )


def convert_step_to_zzx(
    step_path,
    output_directory=None,
    *,
    overwrite=False,
):
    source_path = Path(
        os.path.abspath(
            os.path.expanduser(
                os.fspath(step_path)
            )
        )
    )

    if source_path.suffix.lower() not in {".stp", ".step"}:
        raise StepConversionError(
            "Seleziona un file .stp oppure .step."
        )
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    if output_directory is None:
        destination_directory = source_path.with_name(
            f"{source_path.stem} - ZZX"
        )
    else:
        destination_directory = Path(
            os.path.abspath(
                os.path.expanduser(
                    os.fspath(output_directory)
                )
            )
        )

    started = time.perf_counter()
    api = _step_api()
    _step_log(source_path, "START assembly analysis")

    assembly = _collect_step_definitions(
        source_path,
        api,
    )
    definitions = assembly["definitions"]

    _step_log(
        source_path,
        f"components={len(definitions)} "
        f"occurrences={assembly['occurrence_count']} "
        f"roots={assembly['root_count']}",
    )

    destination_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    results = []
    skipped = []
    errors = []
    converted = 0
    existing = 0
    detected_tubes = 0
    analysed_tubes = []

    with tempfile.TemporaryDirectory(
        prefix="ultra_step_"
    ) as temp_dir:
        temp_root = Path(temp_dir)

        used_output_names = set()

        for index, component in enumerate(definitions, start=1):
            name = _sanitize_component_name(
                component["name"]
                or f"componente_{index:03d}"
            )
            quantity = int(
                component.get("occurrence_count") or 1
            )
            temp_iges = temp_root / f"{index:04d}_{name}.igs"

            _step_log(
                source_path,
                f"[{index}/{len(definitions)}] "
                f"{name} occurrences={quantity}",
            )

            try:
                _export_shape_to_iges(
                    component["shape"],
                    temp_iges,
                    api,
                )
                model = _analyse_iges(temp_iges)
            except IgsConversionError as exc:
                skipped.append(
                    {
                        "componentName": name,
                        "occurrenceCount": quantity,
                        "reason": str(exc),
                    }
                )
                _step_log(
                    source_path,
                    f"SKIP {name}: {exc}",
                )
                continue
            except Exception as exc:
                errors.append(
                    {
                        "componentName": name,
                        "occurrenceCount": quantity,
                        "message": str(exc),
                    }
                )
                _step_log(
                    source_path,
                    f"ERROR {name}: {type(exc).__name__}: {exc}",
                )
                continue

            detected_tubes += 1
            analysed_tubes.append(
                {
                    "name": name,
                    "quantity": quantity,
                    "model": model,
                }
            )

        geometry_groups = OrderedDict()
        for item in analysed_tubes:
            signature = _model_geometry_signature(
                item["model"]
            )
            group = geometry_groups.get(signature)
            if group is None:
                geometry_groups[signature] = {
                    "model": item["model"],
                    "names": [item["name"]],
                    "quantity": int(item["quantity"]),
                    "definition_count": 1,
                }
            else:
                group["names"].append(item["name"])
                group["quantity"] += int(item["quantity"])
                group["definition_count"] += 1

        for group_index, group in enumerate(
            geometry_groups.values(),
            start=1,
        ):
            model = group["model"]
            quantity = int(group["quantity"])
            merged_names = list(group["names"])
            name = _merged_component_name(
                merged_names
            )

            if group["definition_count"] > 1:
                _step_log(
                    source_path,
                    "MERGE identical tube geometry: "
                    f"{group['definition_count']} definitions -> "
                    f"{quantity}pz | "
                    + ", ".join(merged_names),
                )

            output_name = _tube_filename(
                name,
                model,
                quantity,
            )

            # Avoid overwriting another distinct geometry if two groups happen
            # to resolve to the same visible production name.
            base_output_name = output_name
            collision_index = 2
            while output_name.lower() in used_output_names:
                stem = Path(base_output_name).stem
                output_name = (
                    f"{stem} ({collision_index}).zzx"
                )
                collision_index += 1
            used_output_names.add(output_name.lower())

            destination = (
                destination_directory
                / output_name
            )

            result = {
                "componentName": name,
                "componentNames": merged_names,
                "mergedDefinitionCount": int(
                    group["definition_count"]
                ),
                "occurrenceCount": quantity,
                "outputPath": str(destination),
                "profileKind": model["profile_kind"],
                "outsideWidthMm": model["outside_width"],
                "outsideHeightMm": model["outside_height"],
                "thicknessMm": model["thickness"],
                "cornerRadiusMm": model["corner_radius"],
                "lengthMm": model["overall_length"],
                "operationCount": len(model["operations"]),
                "internalOperationCount": max(
                    0,
                    len(model["operations"]) - 2,
                ),
                "displayShapeCount": len(
                    model.get("display_operations") or []
                ),
            }

            if destination.exists() and not overwrite:
                result["status"] = "exists"
                result["message"] = (
                    "Esiste già un file ZZX con lo stesso nome."
                )
                existing += 1
                results.append(result)
                continue

            try:
                pseudo_source = Path(
                    f"{Path(output_name).stem}.igs"
                )
                _write_zzx(
                    model,
                    pseudo_source,
                    destination,
                )
            except Exception as exc:
                result["status"] = "error"
                result["message"] = str(exc)
                results.append(result)
                errors.append(
                    {
                        "componentName": name,
                        "occurrenceCount": quantity,
                        "message": str(exc),
                    }
                )
                _step_log(
                    source_path,
                    f"WRITE ERROR {name}: "
                    f"{type(exc).__name__}: {exc}",
                )
                continue

            result["status"] = "success"
            converted += 1
            results.append(result)
            _step_log(
                source_path,
                f"WRITE OK {name} -> {destination.name}",
            )

    elapsed = time.perf_counter() - started
    _step_log(
        source_path,
        f"END definitions={len(definitions)} "
        f"tube_definitions={detected_tubes} "
        f"unique_geometries={len(geometry_groups)} "
        f"converted={converted} existing={existing} "
        f"skipped={len(skipped)} errors={len(errors)} "
        f"elapsed={elapsed:.3f}s",
    )

    return {
        "status": "success",
        "sourcePath": str(source_path),
        "outputDirectory": str(destination_directory),
        "componentDefinitionCount": len(definitions),
        "componentOccurrenceCount": int(
            assembly["occurrence_count"]
        ),
        "tubeDefinitionCount": detected_tubes,
        "uniqueTubeCount": len(geometry_groups),
        "mergedDefinitionCount": max(
            0,
            detected_tubes - len(geometry_groups),
        ),
        "convertedCount": converted,
        "existingCount": existing,
        "skippedCount": len(skipped),
        "errorCount": len(errors),
        "elapsedSeconds": round(elapsed, 3),
        "results": results,
        "skipped": skipped,
        "errors": errors,
    }
