# Deprecated LumaLink AVStream camera driver prototype

This driver has been abandoned and is **not part of LumaLink Studio's current output path**. The desktop app and installer do not build, install, or communicate with it. Do not install this driver, import its development certificate, or enable test-signing to use LumaLink. Current Windows camera output uses the separately installed OBS Virtual Camera or Unity Capture backends.

The source below is retained only as historical development material and is not production-ready or supported. It was based on Microsoft's `avshws` simulated-hardware sample from `Windows-driver-samples/avstream/avshws`; inherited files retain their Microsoft copyright notices and the included [Microsoft Public License](./LICENSE-MS-PL.txt).

The old build script, installation INF, generated package, and development certificate have been removed. The development machine may still have an old LumaLink device package installed and test-signing enabled from earlier experiments; the current app neither needs nor opens that device. Removing the previously installed package and restoring firmware boot-security settings are separate machine-administration steps and are not performed by this project.
