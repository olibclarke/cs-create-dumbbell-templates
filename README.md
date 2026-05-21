# cs-create-dumbbell-templates
`create_dumbbell_templates.py` builds a simple two-sphere-plus-cylinder volume, applies a Gaussian low-pass, generates projection templates, and saves them as a CryoSPARC External job output for Template Picker.

## Features

- Two spheres with independently configurable diameters
- A center-to-center connecting cylinder
- Default sphere-center separation of `3x` the larger sphere radius

## Requirements

- Python 3.8+
- `cryosparc-tools`
- `numpy`
- `scipy`
- `mrcfile`
- `matplotlib`

## Instance Info

By default the script looks for `~/instance_info.json`. You can also pass it explicitly with `--instance-info /path/to/instance_info.json`.

Example format:

```json
{
    "license": "YOUR_LICENSE_ID",
    "email": "user@example.com",
    "password": "YOUR_PASSWORD",
    "base_port": 39000,
    "host": "YOUR_CRYOSPARC_HOST"
}
```

The `license` value can be found in `cryosparc2_master/config.sh`.

## Basic usage

```bash
python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 20
```

```bash
python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 20 --center-separation 40
```

```bash
python3 create_dumbbell_templates.py P40 W1 --sphere1-diameter 24 --sphere2-diameter 18 --cylinder-diameter 8 --center-separation 45 --angle-step 10
```

## Useful options

- `--sphere1-diameter`: required
- `--sphere2-diameter`: defaults to `--sphere1-diameter`
- `--cylinder-diameter`: default `6 A`
- `--center-separation`: sphere-center separation; default `3x` the larger sphere radius
- `--lowpass-resolution`: default `20 A`
- `--angle-step`: default `10 deg`
- `--pixel-size`: default `2 A/pixel`
- `--help-all`: show advanced output and CryoSPARC options

If both spheres have the same diameter, the script only generates the unique `0-90 deg` views. If the sphere diameters differ, it generates the full `0-180 deg` sweep.

## Outputs

The script creates a CryoSPARC External job and writes:

- `dumbbell_volume_raw.mrc`
- `dumbbell_volume_lowpass.mrc`
- `dumbbell_templates.mrcs`

The job log also reports the maximum end-to-end diameter, which is usually the right starting diameter for Template Picker.

Example output:
<img width="1832" height="1057" alt="Generated_dumbbell_templates_page_1 Panels_are_labeled_with_template_index_and_projection_angle_plus_per_template_min_max_display_values" src="https://github.com/user-attachments/assets/e802c0ad-086c-408d-b656-bfa925f56cf4" />


