#include "frame_bridge.h"
#include <ntintsafe.h>
#include <wdmsec.h>

#define LUMALINK_FRAME_POOL_TAG 'fLmL'
#define LUMALINK_FRAME_SLOT_COUNT 3
#define LUMALINK_FRAME_TIMEOUT_100NS 10000000ULL

typedef struct _LUMALINK_FRAME_SLOT {
    PUCHAR Data;
    ULONG Readers;
    BOOLEAN Writing;
} LUMALINK_FRAME_SLOT, *PLUMALINK_FRAME_SLOT;

typedef struct _LUMALINK_FRAME_STORE {
    KSPIN_LOCK Lock;
    FAST_MUTEX WriterMutex;
    PUCHAR Allocation;
    LUMALINK_FRAME_SLOT Slots[LUMALINK_FRAME_SLOT_COUNT];
    LONG CurrentSlot;
    ULONGLONG LastUpdate;
} LUMALINK_FRAME_STORE;

static LUMALINK_FRAME_STORE g_FrameStore;
static PDEVICE_OBJECT g_ControlDevice;
static UNICODE_STRING g_ControlLinkName;
static PDRIVER_DISPATCH g_PreviousCreate;
static PDRIVER_DISPATCH g_PreviousClose;
static PDRIVER_DISPATCH g_PreviousDeviceControl;
static PDRIVER_UNLOAD g_PreviousUnload;

static const GUID g_LumaLinkDeviceClass =
{
    0x0f4f6b62, 0x2f3a, 0x4e41,
    { 0x93, 0x67, 0x55, 0x6c, 0x75, 0x6d, 0x61, 0x01 }
};

NTSTATUS
LumaLinkInitializeFrameStore()
{
    RtlZeroMemory(&g_FrameStore, sizeof(g_FrameStore));
    KeInitializeSpinLock(&g_FrameStore.Lock);
    ExInitializeFastMutex(&g_FrameStore.WriterMutex);

    const SIZE_T allocationSize =
        LUMALINK_FRAME_BYTES * LUMALINK_FRAME_SLOT_COUNT;
    g_FrameStore.Allocation = reinterpret_cast<PUCHAR>(
        ExAllocatePoolZero(NonPagedPoolNx, allocationSize, LUMALINK_FRAME_POOL_TAG)
    );
    if (g_FrameStore.Allocation == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    for (ULONG index = 0; index < LUMALINK_FRAME_SLOT_COUNT; ++index) {
        g_FrameStore.Slots[index].Data =
            g_FrameStore.Allocation + (index * LUMALINK_FRAME_BYTES);
    }

    g_FrameStore.CurrentSlot = -1;
    return STATUS_SUCCESS;
}

VOID
LumaLinkDestroyFrameStore()
{
    if (g_FrameStore.Allocation != NULL) {
        ExFreePoolWithTag(g_FrameStore.Allocation, LUMALINK_FRAME_POOL_TAG);
        g_FrameStore.Allocation = NULL;
    }

    g_FrameStore.CurrentSlot = -1;
}

NTSTATUS
LumaLinkWriteFrame(
    _In_reads_bytes_(DataBytes) const UCHAR* Data,
    _In_ ULONG DataBytes
)
{
    if (Data == NULL || DataBytes != LUMALINK_FRAME_BYTES ||
        g_FrameStore.Allocation == NULL) {
        return STATUS_INVALID_PARAMETER;
    }

    ExAcquireFastMutex(&g_FrameStore.WriterMutex);

    KIRQL irql;
    LONG selectedSlot = -1;
    KeAcquireSpinLock(&g_FrameStore.Lock, &irql);
    for (LONG index = 0; index < LUMALINK_FRAME_SLOT_COUNT; ++index) {
        PLUMALINK_FRAME_SLOT slot = &g_FrameStore.Slots[index];
        if (index != g_FrameStore.CurrentSlot &&
            slot->Readers == 0 &&
            !slot->Writing) {
            slot->Writing = TRUE;
            selectedSlot = index;
            break;
        }
    }
    KeReleaseSpinLock(&g_FrameStore.Lock, irql);

    if (selectedSlot < 0) {
        ExReleaseFastMutex(&g_FrameStore.WriterMutex);
        return STATUS_DEVICE_BUSY;
    }

    RtlCopyMemory(
        g_FrameStore.Slots[selectedSlot].Data,
        Data,
        LUMALINK_FRAME_BYTES
    );

    KeAcquireSpinLock(&g_FrameStore.Lock, &irql);
    g_FrameStore.Slots[selectedSlot].Writing = FALSE;
    g_FrameStore.CurrentSlot = selectedSlot;
    g_FrameStore.LastUpdate = KeQueryInterruptTime();
    KeReleaseSpinLock(&g_FrameStore.Lock, irql);
    ExReleaseFastMutex(&g_FrameStore.WriterMutex);

    return STATUS_SUCCESS;
}

static UCHAR
ClampByte(_In_ LONG Value)
{
    if (Value < 0) {
        return 0;
    }
    if (Value > 255) {
        return 255;
    }
    return static_cast<UCHAR>(Value);
}

static VOID
ConvertBgr24ToYuy2(
    _In_reads_bytes_(LUMALINK_FRAME_BYTES) const UCHAR* Source,
    _Out_writes_bytes_(DestinationBytes) PUCHAR Destination,
    _In_ ULONG DestinationBytes
)
{
    UNREFERENCED_PARAMETER(DestinationBytes);

    for (ULONG y = 0; y < LUMALINK_FRAME_HEIGHT; ++y) {
        const UCHAR* sourceRow =
            Source + (y * LUMALINK_FRAME_STRIDE);
        PUCHAR destinationRow =
            Destination + (y * LUMALINK_FRAME_WIDTH * 2);

        for (ULONG x = 0; x < LUMALINK_FRAME_WIDTH; x += 2) {
            const UCHAR* pixel0 = sourceRow + (x * 3);
            const UCHAR* pixel1 = pixel0 + 3;

            const LONG b0 = pixel0[0];
            const LONG g0 = pixel0[1];
            const LONG r0 = pixel0[2];
            const LONG b1 = pixel1[0];
            const LONG g1 = pixel1[1];
            const LONG r1 = pixel1[2];

            const LONG y0 = ((66 * r0 + 129 * g0 + 25 * b0 + 128) >> 8) + 16;
            const LONG y1 = ((66 * r1 + 129 * g1 + 25 * b1 + 128) >> 8) + 16;
            const LONG u0 = ((-38 * r0 - 74 * g0 + 112 * b0 + 128) >> 8) + 128;
            const LONG u1 = ((-38 * r1 - 74 * g1 + 112 * b1 + 128) >> 8) + 128;
            const LONG v0 = ((112 * r0 - 94 * g0 - 18 * b0 + 128) >> 8) + 128;
            const LONG v1 = ((112 * r1 - 94 * g1 - 18 * b1 + 128) >> 8) + 128;

            destinationRow[0] = ClampByte(y0);
            destinationRow[1] = ClampByte((u0 + u1 + 1) / 2);
            destinationRow[2] = ClampByte(y1);
            destinationRow[3] = ClampByte((v0 + v1 + 1) / 2);
            destinationRow += 4;
        }
    }
}

BOOLEAN
LumaLinkCopyLatestFrame(
    _Out_writes_bytes_(DestinationBytes) PUCHAR Destination,
    _In_ ULONG DestinationBytes,
    _In_ ULONG Width,
    _In_ ULONG Height,
    _In_ ULONG BytesPerPixel
)
{
    if (Destination == NULL ||
        Width != LUMALINK_FRAME_WIDTH ||
        Height != LUMALINK_FRAME_HEIGHT) {
        return FALSE;
    }

    ULONG expectedBytes = 0;
    if (!NT_SUCCESS(RtlULongMult(Width, Height, &expectedBytes)) ||
        !NT_SUCCESS(RtlULongMult(expectedBytes, BytesPerPixel, &expectedBytes)) ||
        DestinationBytes < expectedBytes) {
        return FALSE;
    }

    KIRQL irql;
    LONG slotIndex;
    PUCHAR source;
    BOOLEAN stale;

    KeAcquireSpinLock(&g_FrameStore.Lock, &irql);
    slotIndex = g_FrameStore.CurrentSlot;
    if (slotIndex < 0) {
        KeReleaseSpinLock(&g_FrameStore.Lock, irql);
        return FALSE;
    }

    PLUMALINK_FRAME_SLOT slot = &g_FrameStore.Slots[slotIndex];
    ++slot->Readers;
    source = slot->Data;
    stale = (KeQueryInterruptTime() - g_FrameStore.LastUpdate) >
        LUMALINK_FRAME_TIMEOUT_100NS;
    KeReleaseSpinLock(&g_FrameStore.Lock, irql);

    if (stale) {
        RtlZeroMemory(Destination, expectedBytes);
    } else if (BytesPerPixel == 3) {
        if (expectedBytes != LUMALINK_FRAME_BYTES) {
            stale = TRUE;
        } else {
            RtlCopyMemory(Destination, source, LUMALINK_FRAME_BYTES);
        }
    } else if (BytesPerPixel == 2) {
        ConvertBgr24ToYuy2(source, Destination, expectedBytes);
    } else {
        stale = TRUE;
    }

    if (stale) {
        RtlZeroMemory(Destination, expectedBytes);
    }

    KeAcquireSpinLock(&g_FrameStore.Lock, &irql);
    --g_FrameStore.Slots[slotIndex].Readers;
    KeReleaseSpinLock(&g_FrameStore.Lock, irql);

    return !stale;
}

static NTSTATUS
LumaLinkCompleteIrp(
    _Inout_ PIRP Irp,
    _In_ NTSTATUS Status,
    _In_ ULONG_PTR Information = 0
)
{
    Irp->IoStatus.Status = Status;
    Irp->IoStatus.Information = Information;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return Status;
}

static NTSTATUS
LumaLinkDispatchCreateClose(
    _In_ PDEVICE_OBJECT DeviceObject,
    _Inout_ PIRP Irp
)
{
    if (DeviceObject != g_ControlDevice) {
        PDRIVER_DISPATCH previous =
            (Irp->Tail.Overlay.CurrentStackLocation->MajorFunction == IRP_MJ_CREATE)
                ? g_PreviousCreate
                : g_PreviousClose;
        return previous != NULL
            ? previous(DeviceObject, Irp)
            : LumaLinkCompleteIrp(Irp, STATUS_INVALID_DEVICE_REQUEST);
    }

    return LumaLinkCompleteIrp(Irp, STATUS_SUCCESS);
}

static NTSTATUS
LumaLinkDispatchDeviceControl(
    _In_ PDEVICE_OBJECT DeviceObject,
    _Inout_ PIRP Irp
)
{
    if (DeviceObject != g_ControlDevice) {
        return g_PreviousDeviceControl != NULL
            ? g_PreviousDeviceControl(DeviceObject, Irp)
            : LumaLinkCompleteIrp(Irp, STATUS_INVALID_DEVICE_REQUEST);
    }

    PIO_STACK_LOCATION stack =
        IoGetCurrentIrpStackLocation(Irp);
    if (stack->Parameters.DeviceIoControl.IoControlCode !=
        LUMALINK_IOCTL_WRITE_FRAME) {
        return LumaLinkCompleteIrp(Irp, STATUS_INVALID_DEVICE_REQUEST);
    }

    const ULONG inputBytes =
        stack->Parameters.DeviceIoControl.InputBufferLength;
    if (inputBytes != sizeof(LUMALINK_FRAME_PACKET) + LUMALINK_FRAME_BYTES ||
        Irp->AssociatedIrp.SystemBuffer == NULL) {
        return LumaLinkCompleteIrp(Irp, STATUS_BUFFER_TOO_SMALL);
    }

    const PLUMALINK_FRAME_PACKET packet =
        reinterpret_cast<PLUMALINK_FRAME_PACKET>(
            Irp->AssociatedIrp.SystemBuffer
        );
    if (packet->Width != LUMALINK_FRAME_WIDTH ||
        packet->Height != LUMALINK_FRAME_HEIGHT ||
        packet->Stride != LUMALINK_FRAME_STRIDE ||
        packet->DataBytes != LUMALINK_FRAME_BYTES) {
        return LumaLinkCompleteIrp(Irp, STATUS_INVALID_PARAMETER);
    }

    const UCHAR* data = reinterpret_cast<const UCHAR*>(packet + 1);
    return LumaLinkCompleteIrp(
        Irp,
        LumaLinkWriteFrame(data, packet->DataBytes)
    );
}

NTSTATUS
LumaLinkCreateControlDevice(_In_ PDRIVER_OBJECT DriverObject)
{
    UNICODE_STRING deviceName;
    UNICODE_STRING defaultSddl;
    RtlInitUnicodeString(&deviceName, L"\\Device\\LumaLinkCamera");
    RtlInitUnicodeString(
        &defaultSddl,
        L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"
    );

    NTSTATUS status = IoCreateDeviceSecure(
        DriverObject,
        0,
        &deviceName,
        FILE_DEVICE_VIDEO,
        FILE_DEVICE_SECURE_OPEN,
        FALSE,
        &defaultSddl,
        &g_LumaLinkDeviceClass,
        &g_ControlDevice
    );
    if (!NT_SUCCESS(status)) {
        return status;
    }

    RtlInitUnicodeString(&g_ControlLinkName, L"\\DosDevices\\LumaLinkCamera");
    status = IoCreateSymbolicLink(&g_ControlLinkName, &deviceName);
    if (!NT_SUCCESS(status)) {
        IoDeleteDevice(g_ControlDevice);
        g_ControlDevice = NULL;
        return status;
    }

    g_ControlDevice->Flags |= DO_BUFFERED_IO;
    g_ControlDevice->Flags &= ~DO_DEVICE_INITIALIZING;
    return STATUS_SUCCESS;
}

VOID
LumaLinkInstallDispatch(_In_ PDRIVER_OBJECT DriverObject)
{
    g_PreviousCreate = DriverObject->MajorFunction[IRP_MJ_CREATE];
    g_PreviousClose = DriverObject->MajorFunction[IRP_MJ_CLOSE];
    g_PreviousDeviceControl =
        DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL];
    g_PreviousUnload = DriverObject->DriverUnload;

    DriverObject->MajorFunction[IRP_MJ_CREATE] = LumaLinkDispatchCreateClose;
    DriverObject->MajorFunction[IRP_MJ_CLOSE] = LumaLinkDispatchCreateClose;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] =
        LumaLinkDispatchDeviceControl;
    DriverObject->DriverUnload = LumaLinkDriverUnload;
}

VOID
LumaLinkDestroyControlDevice()
{
    if (g_ControlDevice != NULL) {
        IoDeleteSymbolicLink(&g_ControlLinkName);
        IoDeleteDevice(g_ControlDevice);
        g_ControlDevice = NULL;
    }
}

VOID
LumaLinkDriverUnload(_In_ PDRIVER_OBJECT DriverObject)
{
    LumaLinkDestroyControlDevice();
    LumaLinkDestroyFrameStore();

    if (g_PreviousUnload != NULL) {
        g_PreviousUnload(DriverObject);
    }
}
