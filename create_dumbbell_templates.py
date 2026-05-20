#!/usr/bin/env python3
"""Create synthetic dumbbell templates for CryoSPARC."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import math
import sys
import warnings
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np


DEFAULT_CYLINDER_DIAMETER_A = 6.0
DEFAULT_LOWPASS_RESOLUTION_A = 20.0
DEFAULT_ANGLE_STEP_DEG = 10.0
DEFAULT_PIXEL_SIZE_A = 2.0


class DefaultsRawFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError(f"Expected a positive finite value, got {value!r}")
    return parsed


def bounded_angle_step(value: str) -> float:
    parsed = positive_float(value)
    if parsed > 180.0:
        raise argparse.ArgumentTypeError("Projection angle step must be <= 180 degrees")
    return parsed


def ensure_prefix(value: str, prefix: str) -> str:
    text = str(value).strip().upper()
    return text if text.startswith(prefix) else f"{prefix}{text}"


def dependency_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def check_runtime_dependencies() -> None:
    if sys.version_info < (3, 8):
        raise RuntimeError(
            "create_dumbbell_templates.py requires Python 3.8 or newer. "
            f"Current interpreter: {sys.version.split()[0]}"
        )

    missing: List[Tuple[str, str]] = []
    for module_name, install_hint in (
        ("mrcfile", "pip install mrcfile"),
        ("scipy.ndimage", "pip install scipy"),
        ("matplotlib", "pip install matplotlib"),
        ("cryosparc.tools", "pip install cryosparc-tools"),
    ):
        if not dependency_available(module_name):
            missing.append((module_name, install_hint))

    if missing:
        lines = ["Missing required runtime dependencies for create_dumbbell_templates.py:"]
        for module_name, install_hint in missing:
            lines.append(f"- {module_name} ({install_hint})")
        lines.extend(
            [
                "",
                "Run this script with the same Python environment that has cryosparc-tools installed,",
                "or install the missing package(s) into the environment you are using.",
            ]
        )
        raise RuntimeError("\n".join(lines))


def connect_cryosparc(instance_info_path: str):
    from cryosparc.tools import CryoSPARC

    info_path = Path(instance_info_path).expanduser()
    if not info_path.exists():
        raise RuntimeError(
            f"Could not find CryoSPARC instance info at {info_path}.\n"
            "Pass --instance-info /path/to/instance_info.json or create that file.\n"
            "Expected JSON keys: license, email, password, base_port, host."
        )

    try:
        data = json.loads(info_path.read_text())
    except Exception as exc:
        raise RuntimeError(f"Failed to read CryoSPARC instance info from {info_path}") from exc

    try:
        cs = CryoSPARC(**data)
    except Exception as exc:
        raise RuntimeError(f"Failed to authenticate using instance info {info_path}") from exc

    if not cs.test_connection():
        raise RuntimeError(f"Could not connect to CryoSPARC using instance info {info_path}")
    return cs, f"instance info {info_path}"


def infer_project_dir(project, override: Optional[str]) -> Path:
    if override:
        project_dir = Path(override).expanduser().resolve()
        if not project_dir.exists():
            raise FileNotFoundError(f"--project-dir does not exist: {project_dir}")
        return project_dir

    for attr_name in ("dir", "project_dir", "path"):
        attr = getattr(project, attr_name, None)
        if attr is None:
            continue
        if callable(attr):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                try:
                    value = attr()
                except TypeError:
                    continue
        else:
            value = attr
        if value:
            project_dir = Path(str(value)).expanduser().resolve()
            if project_dir.exists():
                return project_dir

    raise RuntimeError(
        "Could not infer the CryoSPARC project directory from cryosparc-tools. "
        "Please provide --project-dir explicitly."
    )


def project_relative_path(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except Exception:
        return str(path.resolve())


def create_external_job_with_optional_description(project, workspace_uid: str, title: str, description: str):
    create_kwargs = dict(workspace_uid=workspace_uid, title=title)
    if description:
        try:
            parameter_names = set(inspect.signature(project.create_external_job).parameters)
        except Exception:
            parameter_names = set()
        if "desc" in parameter_names:
            create_kwargs["desc"] = description
        elif "description" in parameter_names:
            create_kwargs["description"] = description
    return project.create_external_job(**create_kwargs)


def existing_field(dataset, field: str) -> bool:
    try:
        dataset[field]
        return True
    except Exception:
        return False


def write_mrc(path: Path, data: np.ndarray, voxel_size_angstrom: float) -> None:
    import mrcfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with mrcfile.new(path, overwrite=True) as handle:
        handle.set_data(np.asarray(data, dtype=np.float32))
        handle.voxel_size = float(voxel_size_angstrom)
        handle.update_header_stats()


def auto_separation_A(radius1_A: float, radius2_A: float) -> float:
    return 3.0 * max(float(radius1_A), float(radius2_A))


def maximum_dumbbell_diameter_A(
    *,
    sphere1_radius_A: float,
    sphere2_radius_A: float,
    cylinder_radius_A: float,
    center_separation_A: float,
) -> float:
    axial_extent_A = float(center_separation_A) + float(sphere1_radius_A) + float(sphere2_radius_A)
    transverse_extent_A = 2.0 * max(float(sphere1_radius_A), float(sphere2_radius_A), float(cylinder_radius_A))
    return max(axial_extent_A, transverse_extent_A)


def even_box_size(pixel_size_A: float, physical_box_A: float) -> int:
    box_size = int(math.ceil(float(physical_box_A) / float(pixel_size_A)))
    box_size = max(box_size, 16)
    if box_size % 2 != 0:
        box_size += 1
    return box_size


def centered_coordinates_A(box_size: int, pixel_size_A: float) -> np.ndarray:
    return (np.arange(int(box_size), dtype=np.float32) - 0.5 * float(box_size - 1)) * float(pixel_size_A)


def build_dumbbell_volume(
    *,
    box_size: int,
    pixel_size_A: float,
    sphere1_radius_A: float,
    sphere2_radius_A: float,
    cylinder_radius_A: float,
    center_separation_A: float,
) -> np.ndarray:
    coords = centered_coordinates_A(box_size, pixel_size_A)
    z_grid = coords[:, None, None]
    y_grid = coords[None, :, None]
    x_grid = coords[None, None, :]

    sphere1_center_x_A = -0.5 * float(center_separation_A)
    sphere2_center_x_A = 0.5 * float(center_separation_A)
    radial_yz_A2 = y_grid * y_grid + z_grid * z_grid
    sphere1_mask = ((x_grid - sphere1_center_x_A) ** 2 + radial_yz_A2) <= float(sphere1_radius_A) ** 2
    sphere2_mask = ((x_grid - sphere2_center_x_A) ** 2 + radial_yz_A2) <= float(sphere2_radius_A) ** 2
    cylinder_mask = (
        (radial_yz_A2 <= float(cylinder_radius_A) ** 2)
        & (x_grid >= sphere1_center_x_A)
        & (x_grid <= sphere2_center_x_A)
    )
    return (sphere1_mask | sphere2_mask | cylinder_mask).astype(np.float32, copy=False)


def gaussian_sigma_A_for_half_amplitude_resolution(resolution_A: float) -> float:
    return float(resolution_A) * math.sqrt(math.log(2.0)) / (2.0 * math.pi)


def gaussian_lowpass_volume(volume: np.ndarray, pixel_size_A: float, resolution_A: float) -> np.ndarray:
    from scipy.ndimage import gaussian_filter

    sigma_A = gaussian_sigma_A_for_half_amplitude_resolution(float(resolution_A))
    sigma_px = sigma_A / float(pixel_size_A)
    if sigma_px <= 0.0:
        return np.asarray(volume, dtype=np.float32, copy=True)
    filtered = gaussian_filter(
        np.asarray(volume, dtype=np.float32),
        sigma=sigma_px,
        mode="constant",
        cval=0.0,
    )
    return np.asarray(filtered, dtype=np.float32)


def projection_angles_deg(step_deg: float, *, max_angle_deg: float = 180.0, include_endpoint: bool = False) -> np.ndarray:
    angles: List[float] = []
    angle = 0.0
    cutoff_deg = float(max_angle_deg)
    while angle < cutoff_deg - 1e-8:
        angles.append(round(angle, 8))
        angle += float(step_deg)
    if include_endpoint and (not angles or abs(angles[-1] - cutoff_deg) > 1e-8):
        angles.append(round(cutoff_deg, 8))
    if not angles:
        angles.append(0.0)
    return np.asarray(angles, dtype=np.float32)


def generate_projection_stack(volume: np.ndarray, angles_deg: Sequence[float]) -> np.ndarray:
    from scipy.ndimage import rotate

    angle_values = np.asarray(list(angles_deg), dtype=np.float32)
    if angle_values.ndim != 1 or angle_values.size == 0:
        raise ValueError("At least one projection angle is required")

    stack = np.empty((int(angle_values.size), int(volume.shape[1]), int(volume.shape[2])), dtype=np.float32)
    base_volume = np.asarray(volume, dtype=np.float32)

    for index, angle_deg in enumerate(angle_values.tolist()):
        if abs(float(angle_deg)) < 1e-8:
            rotated = base_volume
        else:
            rotated = rotate(
                base_volume,
                angle=float(angle_deg),
                axes=(2, 0),
                reshape=False,
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
        stack[index] = np.sum(rotated, axis=0, dtype=np.float32)

    return stack


def write_angle_table(path: Path, angles_deg: Sequence[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["template_index\tangle_deg"]
    for index, angle_deg in enumerate(np.asarray(angles_deg).tolist()):
        lines.append(f"{index}\t{float(angle_deg):.6f}")
    path.write_text("\n".join(lines) + "\n")


def build_template_montage_figure(
    projection_stack: np.ndarray,
    angles_deg: Sequence[float],
    *,
    start_index: int,
    stop_index: int,
    columns: int = 6,
):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    page_stack = np.asarray(projection_stack[start_index:stop_index], dtype=np.float32)
    page_angles = np.asarray(list(angles_deg)[start_index:stop_index], dtype=np.float32)
    image_count = int(page_stack.shape[0])
    if image_count <= 0:
        raise ValueError("Cannot build a montage for an empty template page")

    columns = max(1, int(columns))
    rows = int(math.ceil(float(image_count) / float(columns)))
    figure, axes = plt.subplots(rows, columns, figsize=(2.2 * columns, 2.6 * rows), dpi=140)
    axes_array = np.atleast_1d(axes).ravel()

    for axis in axes_array:
        axis.set_axis_off()

    for local_index in range(image_count):
        axis = axes_array[local_index]
        image = page_stack[local_index]
        angle_deg = float(page_angles[local_index])
        image_min = float(np.min(image)) if image.size else 0.0
        image_max = float(np.max(image)) if image.size else 0.0
        if (not math.isfinite(image_min)) or (not math.isfinite(image_max)) or image_min == image_max:
            image_min = 0.0
            image_max = 1.0
        axis.imshow(image, cmap="gray", origin="lower", vmin=image_min, vmax=image_max)
        axis.set_title(
            f"#{start_index + local_index} | {angle_deg:.1f} deg\n"
            f"min={image_min:.3g}, max={image_max:.3g}",
            fontsize=8,
        )
        axis.set_axis_off()

    figure.tight_layout(pad=0.4, w_pad=0.2, h_pad=0.8)
    return figure


def log_template_montages(
    external_job,
    projection_stack: np.ndarray,
    angles_deg: Sequence[float],
    output_dir: Path,
    *,
    max_templates_per_page: int = 36,
) -> int:
    template_count = int(np.asarray(projection_stack).shape[0])
    if template_count <= 0:
        return 0

    page_count = 0
    for start_index in range(0, template_count, int(max_templates_per_page)):
        stop_index = min(template_count, start_index + int(max_templates_per_page))
        figure = build_template_montage_figure(
            projection_stack,
            angles_deg,
            start_index=start_index,
            stop_index=stop_index,
        )
        page_count += 1
        page_path = output_dir / f"template_montage_page_{page_count:02d}.png"
        figure.savefig(page_path, dpi=140, bbox_inches="tight", pad_inches=0.04)
        external_job.log_plot(
            figure,
            text=(
                f"Generated dumbbell templates, page {page_count}. "
                "Panels are labeled with template index and projection angle, plus per-template min/max display values."
            ),
            formats=["png"],
        )
        try:
            import matplotlib.pyplot as plt

            plt.close(figure)
        except Exception:
            pass

    return page_count


def assign_template_blob_fields(dataset, *, stack_rel_path: str, box_size: int, pixel_size_A: float) -> None:
    template_count = int(len(dataset))
    path_values = np.asarray([stack_rel_path] * template_count)
    idx_values = np.arange(template_count, dtype=np.int32)
    shape_values = np.repeat(np.asarray([[box_size, box_size]], dtype=np.uint32), template_count, axis=0)
    psize_values = np.full(template_count, float(pixel_size_A), dtype=np.float32)
    sign_values = np.ones(template_count, dtype=np.int32)

    required_fields = ("blob/path", "blob/idx")
    for field_name in required_fields:
        if not existing_field(dataset, field_name):
            raise RuntimeError(
                f"Allocated template output is missing required field {field_name!r}. "
                "Expected a template output with a blob slot."
            )

    dataset["blob/path"] = path_values
    dataset["blob/idx"] = idx_values
    if existing_field(dataset, "blob/shape"):
        dataset["blob/shape"] = shape_values
    if existing_field(dataset, "blob/psize_A"):
        dataset["blob/psize_A"] = psize_values
    if existing_field(dataset, "blob/sign"):
        dataset["blob/sign"] = sign_values


def basic_help_epilog() -> str:
    return (
        "Common examples:\n"
        "  python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 20\n"
        "  python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 20 --center-separation 40\n"
        "  python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 24 --sphere2-diameter 18 --cylinder-diameter 8\n"
        "\n"
        "Use --help-all to show every available option."
    )


def add_common_arguments(parser: argparse.ArgumentParser, *, show_all: bool) -> None:
    parser.add_argument("project_uid", help="CryoSPARC project UID, with or without the P prefix")
    parser.add_argument("workspace_uid", help="CryoSPARC workspace UID, with or without the W prefix")
    parser.add_argument(
        "--sphere1-diameter",
        type=positive_float,
        required=True,
        help="Diameter of sphere 1 in Angstrom",
    )
    parser.add_argument(
        "--sphere2-diameter",
        type=positive_float,
        help="Optional diameter of sphere 2 in Angstrom; defaults to sphere 1 diameter",
    )
    parser.add_argument(
        "--cylinder-diameter",
        type=positive_float,
        default=DEFAULT_CYLINDER_DIAMETER_A,
        help="Cylinder diameter in Angstrom",
    )
    parser.add_argument(
        "--center-separation",
        type=positive_float,
        help="Sphere-center separation in Angstrom; defaults to 3x the larger sphere radius",
    )
    parser.add_argument(
        "--lowpass-resolution",
        type=positive_float,
        default=DEFAULT_LOWPASS_RESOLUTION_A,
        help="Gaussian low-pass resolution in Angstrom",
    )
    parser.add_argument(
        "--angle-step",
        type=bounded_angle_step,
        default=DEFAULT_ANGLE_STEP_DEG,
        help="Projection increment in degrees",
    )
    parser.add_argument(
        "--pixel-size",
        type=positive_float,
        default=DEFAULT_PIXEL_SIZE_A,
        help="Pixel size in Angstrom/pixel for the synthetic volume and template stack",
    )
    if show_all:
        parser.add_argument(
            "--output-name",
            default="templates",
            help="CryoSPARC output group name for the generated templates",
        )
        parser.add_argument(
            "--title",
            default="Create Dumbbell Templates",
            help="CryoSPARC External job title",
        )
        parser.add_argument(
            "--instance-info",
            default="~/instance_info.json",
            help="Path to CryoSPARC instance_info.json",
        )
        parser.add_argument(
            "--project-dir",
            help="Optional explicit path to the CryoSPARC project directory if cryosparc-tools cannot infer it",
        )
        parser.add_argument(
            "--output-subdir",
            default="dumbbell_templates",
            help="Subdirectory inside the External job folder for generated artifacts",
        )
    else:
        parser.set_defaults(
            output_name="templates",
            title="Create Dumbbell Templates",
            instance_info="~/instance_info.json",
            project_dir=None,
            output_subdir="dumbbell_templates",
        )


def build_arg_parser(*, show_all: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create synthetic dumbbell templates for CryoSPARC Template Picker.",
        formatter_class=DefaultsRawFormatter,
        epilog=basic_help_epilog() if not show_all else None,
    )
    add_common_arguments(parser, show_all=show_all)
    if show_all:
        parser.add_argument("--help-all", action="store_true", help=argparse.SUPPRESS)
    return parser


def maybe_handle_help(raw_argv: Sequence[str]) -> bool:
    if "--help-all" in raw_argv:
        print(build_arg_parser(show_all=True).format_help())
        return True
    if "-h" in raw_argv or "--help" in raw_argv:
        print(build_arg_parser(show_all=False).format_help())
        return True
    return False


def main() -> None:
    raw_argv = sys.argv[1:]
    if maybe_handle_help(raw_argv):
        return
    check_runtime_dependencies()
    parser = build_arg_parser(show_all=True)
    args = parser.parse_args(raw_argv)

    project_uid = ensure_prefix(args.project_uid, "P")
    workspace_uid = ensure_prefix(args.workspace_uid, "W")
    sphere1_diameter_A = float(args.sphere1_diameter)
    sphere2_diameter_A = float(args.sphere2_diameter or args.sphere1_diameter)
    cylinder_diameter_A = float(args.cylinder_diameter)
    sphere1_radius_A = 0.5 * sphere1_diameter_A
    sphere2_radius_A = 0.5 * sphere2_diameter_A
    cylinder_radius_A = 0.5 * cylinder_diameter_A
    center_separation_A = float(args.center_separation or auto_separation_A(sphere1_radius_A, sphere2_radius_A))
    max_radius_A = max(sphere1_radius_A, sphere2_radius_A)
    max_diameter_A = maximum_dumbbell_diameter_A(
        sphere1_radius_A=sphere1_radius_A,
        sphere2_radius_A=sphere2_radius_A,
        cylinder_radius_A=cylinder_radius_A,
        center_separation_A=center_separation_A,
    )

    geometric_half_extent_A = 0.5 * center_separation_A + max_radius_A
    padding_A = max(float(args.lowpass_resolution), 8.0)
    physical_box_A = 2.0 * (geometric_half_extent_A + padding_A)
    pixel_size_A = float(args.pixel_size)
    box_size = even_box_size(pixel_size_A, physical_box_A)
    actual_box_A = box_size * pixel_size_A
    symmetric_spheres = math.isclose(sphere1_diameter_A, sphere2_diameter_A, rel_tol=0.0, abs_tol=1e-6)
    projection_max_angle_deg = 90.0 if symmetric_spheres else 180.0
    include_projection_endpoint = symmetric_spheres
    angles_deg = projection_angles_deg(
        float(args.angle_step),
        max_angle_deg=projection_max_angle_deg,
        include_endpoint=include_projection_endpoint,
    )
    projection_range_text = (
        "[0,90]" if symmetric_spheres else "[0,180)"
    )

    cs, auth_source = connect_cryosparc(args.instance_info)
    project = cs.find_project(project_uid)
    project_dir = infer_project_dir(project, args.project_dir)

    description = (
        f"Synthetic dumbbell template generator\n"
        f"Sphere diameters: {sphere1_diameter_A:g} A, {sphere2_diameter_A:g} A\n"
        f"Cylinder diameter: {cylinder_diameter_A:g} A\n"
        f"Center separation: {center_separation_A:g} A\n"
        f"Gaussian low-pass: {float(args.lowpass_resolution):g} A\n"
        f"Projection sweep: axis=y, step={float(args.angle_step):g} deg, angles={projection_range_text}"
    )
    external_job = create_external_job_with_optional_description(project, workspace_uid, args.title, description)
    output_templates = external_job.add_output(type="template", name=args.output_name, slots=["blob"], alloc=len(angles_deg))

    output_dir = project_dir / external_job.uid / args.output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_volume_path = output_dir / "dumbbell_volume_raw.mrc"
    lowpass_volume_path = output_dir / "dumbbell_volume_lowpass.mrc"
    template_stack_path = output_dir / "dumbbell_templates.mrcs"
    angle_table_path = output_dir / "projection_angles.tsv"

    with external_job.run():
        external_job.log(f"Authenticated via {auth_source}")
        external_job.log(f"Project directory: {project_dir}")
        external_job.log(
            f"Geometry: sphere1={sphere1_diameter_A:g} A, sphere2={sphere2_diameter_A:g} A, "
            f"cylinder={cylinder_diameter_A:g} A, center separation={center_separation_A:g} A"
        )
        external_job.log(
            f"Maximum dumbbell diameter: {max_diameter_A:g} A "
            f"(use this as the Template Picker diameter if you want the full end-to-end size)"
        )
        external_job.log(
            f"Sampling: pixel size={pixel_size_A:.4f} A/px, box={box_size}px, "
            f"physical box={actual_box_A:.4f} A"
        )
        external_job.log(
            f"Projection sweep: {len(angles_deg)} template(s), step={float(args.angle_step):g} deg over {projection_range_text}"
        )

        volume = build_dumbbell_volume(
            box_size=box_size,
            pixel_size_A=pixel_size_A,
            sphere1_radius_A=sphere1_radius_A,
            sphere2_radius_A=sphere2_radius_A,
            cylinder_radius_A=cylinder_radius_A,
            center_separation_A=center_separation_A,
        )
        write_mrc(raw_volume_path, volume, pixel_size_A)
        external_job.log(f"Wrote raw synthetic volume: {raw_volume_path.name}")
        external_job.log(
            f"Raw volume density range: min={float(np.min(volume)):.6g}, max={float(np.max(volume)):.6g}"
        )

        lowpassed_volume = gaussian_lowpass_volume(volume, pixel_size_A, float(args.lowpass_resolution))
        write_mrc(lowpass_volume_path, lowpassed_volume, pixel_size_A)
        external_job.log(
            f"Wrote Gaussian low-passed volume: {lowpass_volume_path.name} "
            f"(half-amplitude at {float(args.lowpass_resolution):g} A)"
        )
        external_job.log(
            f"Low-passed volume density range: min={float(np.min(lowpassed_volume)):.6g}, max={float(np.max(lowpassed_volume)):.6g}"
        )

        projection_stack = generate_projection_stack(lowpassed_volume, angles_deg)
        write_mrc(template_stack_path, projection_stack, pixel_size_A)
        write_angle_table(angle_table_path, angles_deg)
        external_job.log(f"Wrote template stack: {template_stack_path.name}")
        external_job.log(f"Wrote projection angle table: {angle_table_path.name}")
        montage_page_count = log_template_montages(
            external_job,
            projection_stack,
            angles_deg,
            output_dir,
        )
        external_job.log(f"Logged template montage page(s): {montage_page_count}")

        template_stack_rel_path = project_relative_path(project_dir, template_stack_path)
        assign_template_blob_fields(
            output_templates,
            stack_rel_path=template_stack_rel_path,
            box_size=box_size,
            pixel_size_A=pixel_size_A,
        )
        external_job.save_output(args.output_name, output_templates)
        external_job.log(f"Saved template output: {project_uid}/{external_job.uid}:{args.output_name}")

    print(f"Saved templates to {project_uid}/{external_job.uid}:{args.output_name}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
