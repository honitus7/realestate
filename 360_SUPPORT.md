# 360-Degree Panorama Support

## Overview

The application now supports **360-degree equirectangular panoramas** in addition to regular flat panoramas. When you upload a 360-degree image, it will be displayed using Photo Sphere Viewer, allowing users to look around in all directions.

## How to Use

### Uploading 360-Degree Images

1. Go to the landing page (`/`)
2. Upload your panorama image (drag & drop or click to browse)
3. **Check the "360° Equirectangular Image" checkbox** before creating the project
4. Enter a project name and click "Create Project"

### Image Requirements

- **Format**: Equirectangular projection (2:1 aspect ratio recommended)
- **File Types**: JPG, PNG, WebP
- **Size**: Up to 50MB
- **Aspect Ratio**: Typically 2:1 (e.g., 8192×4096, 4096×2048)

### Features in 360 Mode

✅ **What Works:**
- Full 360° viewing (pan up/down/left/right)
- Zoom in/out
- Fullscreen mode
- Download option
- Smooth navigation controls
- View-only sharing links

⚠️ **Current Limitations:**
- **Plot marking in 360° mode is limited** - The current plot marking system uses 2D coordinates, which don't translate directly to 3D spherical coordinates
- For full plot marking support in 360° images, you'll need to use regular flat panoramas
- 360° mode is best for viewing and exploration

## Technical Details

### How It Works

- **Regular Panoramas**: Uses 2D canvas/SVG for plot marking
- **360° Panoramas**: Uses Photo Sphere Viewer (Three.js-based) for immersive viewing

### Detection

The system automatically detects whether a panorama is 360° based on the `is_360` flag set during upload. The viewer adapts accordingly:

- **360° images**: Rendered with Photo Sphere Viewer
- **Regular images**: Rendered with the standard 2D viewer

### Database

The `panoramas` table includes an `is_360` column:
- `0` = Regular panorama (default)
- `1` = 360-degree panorama

## Future Enhancements

Potential improvements for 360° mode:

1. **3D Plot Marking**: Implement spherical coordinate system for plot boundaries
2. **Hotspots**: Add clickable markers positioned in 3D space
3. **Multiple Panoramas**: Link between different 360° views
4. **VR Support**: Add WebXR support for VR headsets
5. **Gyroscope**: Mobile device orientation support

## Libraries Used

- **Pannellum v2.5.6**: For 360° image rendering (lightweight, no dependencies)

## Use Cases

**360° Mode is Perfect For:**
- Property tours
- Virtual site visits
- Immersive exploration
- Client presentations
- Marketing materials

**Regular Mode is Better For:**
- Plot mapping and marking
- Precise area measurements
- Detailed annotations
- Property planning

## Switching Between Modes

You can have both types of panoramas in your project:
- Upload regular panoramas for plot marking
- Upload 360° panoramas for immersive viewing
- Each panorama type works independently

---

**Note**: If you need plot marking functionality, use regular panoramas. 360° mode is optimized for viewing and exploration.

