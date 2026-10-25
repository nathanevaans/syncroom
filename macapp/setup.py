"""Builds SyncRoom.app with py2app — run macapp/build.sh rather than this directly."""

from setuptools import setup

from syncroom import __version__

setup(
    name="SyncRoom",
    app=["main.py"],
    options={
        "py2app": {
            "plist": {
                "CFBundleName": "SyncRoom",
                "CFBundleDisplayName": "SyncRoom",
                "CFBundleIdentifier": "local.syncroom",
                "CFBundleShortVersionString": __version__,
                "CFBundleVersion": __version__,
                "LSUIElement": True,  # menu bar only, no Dock icon
                "LSMinimumSystemVersion": "13.0",  # ScreenCaptureKit audio
                "NSMicrophoneUsageDescription": (
                    "SyncRoom listens for its calibration chirps to line your speakers up."
                ),
                "NSLocalNetworkUsageDescription": (
                    "SyncRoom streams music to your Sonos and Google Home speakers."
                ),
            },
        }
    },
)
