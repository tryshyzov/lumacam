// What LumaLink Studio and the LumaLink Camera extension agree on.

import Foundation

enum CameraConfig {
    static let name = "LumaLink Camera"
    static let manufacturer = "LumaLink"
    static let extensionIdentifier = "com.lumalink.studio.camera"
    // Fixed, so applications remember the camera across restarts.
    static let deviceID = UUID(uuidString: "6C8E2B41-3F7A-4D2E-9B1C-5A7D0E4F8C21")!
    static let sourceStreamID = UUID(uuidString: "A1F4C7D2-8E3B-4F6A-B5D9-2C0E7A1B3F54")!
    static let sinkStreamID = UUID(uuidString: "D3B9E5A8-1C7F-4E2D-8A6B-9F0C4E2D7B13")!
    // The size and rate LumaLink Studio sends (camera_studio/virtual_camera.py).
    static let width: Int32 = 1280
    static let height: Int32 = 720
    static let frameRate: Int32 = 30
    // After this long without a frame from LumaLink Studio, show the idle image.
    static let frameTimeout: TimeInterval = 1.0
}
