#ifndef LUMALINK_FRAME_BRIDGE_H
#define LUMALINK_FRAME_BRIDGE_H

#include "avshws.h"

#define LUMALINK_FRAME_WIDTH 1280
#define LUMALINK_FRAME_HEIGHT 720
#define LUMALINK_FRAME_STRIDE (LUMALINK_FRAME_WIDTH * 3)
#define LUMALINK_FRAME_BYTES (LUMALINK_FRAME_STRIDE * LUMALINK_FRAME_HEIGHT)
#define LUMALINK_IOCTL_WRITE_FRAME \
    CTL_CODE(FILE_DEVICE_VIDEO, 0x800, METHOD_BUFFERED, FILE_WRITE_DATA)

typedef struct _LUMALINK_FRAME_PACKET {
    ULONG Width;
    ULONG Height;
    ULONG Stride;
    ULONG DataBytes;
} LUMALINK_FRAME_PACKET, *PLUMALINK_FRAME_PACKET;

NTSTATUS LumaLinkInitializeFrameStore();
VOID LumaLinkDestroyFrameStore();
NTSTATUS LumaLinkWriteFrame(
    _In_reads_bytes_(DataBytes) const UCHAR* Data,
    _In_ ULONG DataBytes
);
BOOLEAN LumaLinkCopyLatestFrame(
    _Out_writes_bytes_(DestinationBytes) PUCHAR Destination,
    _In_ ULONG DestinationBytes,
    _In_ ULONG Width,
    _In_ ULONG Height,
    _In_ ULONG BytesPerPixel
);

NTSTATUS LumaLinkCreateControlDevice(_In_ PDRIVER_OBJECT DriverObject);
VOID LumaLinkInstallDispatch(_In_ PDRIVER_OBJECT DriverObject);
VOID LumaLinkDestroyControlDevice();
VOID LumaLinkDriverUnload(_In_ PDRIVER_OBJECT DriverObject);

#endif
