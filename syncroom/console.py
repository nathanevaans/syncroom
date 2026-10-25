"""Interactive `syncroom>` prompt."""

import time


def run_console(hub):
    help_ = (
        "commands:  c = calibrate all   c <name> = calibrate one\n"
        "           t [name] = sync test (4 clicks)\n"
        "           lat <name> <ms> = set a speaker's latency\n"
        "           delay <name> <ms> = set its added delay\n"
        "           r <name> = make a speaker play again   off <name> = leave a speaker alone\n"
        "           rec = save 10 s of the captured audio to a WAV file\n"
        "           s = status          +10 <name> / -10 <name> = trim ms   q = quit"
    )
    print(help_)
    while True:
        parts = input("syncroom> ").split()
        if not parts:
            continue
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else None)
        if arg and arg not in hub.outputs:
            print(f"unknown speaker '{arg}'. speakers: {', '.join(hub.outputs)}")
        elif hub.calibrating and (cmd in ("lat", "delay") or cmd[0] in "+-"):
            print("calibrating — delays are locked until it finishes")
        elif cmd == "c":
            hub.calibrate([arg] if arg else None, ask=input)
        elif cmd == "lat" and arg and len(parts) > 2:
            hub.cfg["latency"][arg] = max(0.0, float(parts[2]) / 1000)
            hub.outputs[arg].stale = False
            hub.save()
            print(
                f"{arg} latency set to {float(parts[2]):.0f} ms; global delay now "
                f"{hub.global_delay() * 1000:.0f} ms"
            )
        elif cmd == "delay" and arg and len(parts) > 2:
            hub.set_added_delay(arg, parts[2])
            print(f"{arg} added delay now {hub.offset_of(arg) * 1000:.0f} ms")
        elif cmd == "r" and arg:
            hub.outputs[arg].paused = False
            hub.outputs[arg].start()
            print(f"told {arg} to play again (then run `c {arg}` to recalibrate it)")
        elif cmd == "off" and arg:
            hub.outputs[arg].paused = True
            print(f"{arg}: SyncRoom won't take it back over until you type `r {arg}`")
        elif cmd == "rec":
            hub.record_source()
        elif cmd == "t":
            hub.sync_test([arg] if arg else None)
            print("listen: 4 clicks — one sharp click each means aligned")
        elif cmd == "s":
            ov = getattr(hub.src.proc, "overflows", None)
            print(
                f"global delay {hub.global_delay() * 1000:.0f} ms   "
                f"source level {hub.src_level():.0f} dB"
                + (f"   capture overflows {ov}" if ov is not None else "")
            )
            for n, o in hub.outputs.items():
                m = "measured" if n in hub.cfg["latency"] else "guess"
                fresh = time.monotonic() - o.level_t < 2
                lvl = f"{o.level_db:5.0f} dB" if fresh else "  no data"
                print(
                    f"  {n:24} {o.status:10} sending {lvl}  "
                    f"latency {hub.latency(n) * 1000:6.0f} ms ({m})  "
                    f"trim {hub.cfg['trim_ms'].get(n, 0):+.0f} ms"
                    + ("  RECONNECTED - recalibrate" if o.stale else "")
                )
        elif cmd[0] in "+-" and cmd[1:].isdigit() and arg:
            hub.cfg["trim_ms"][arg] = hub.cfg["trim_ms"].get(arg, 0) + float(cmd)
            hub.save()
            print(f"{arg} trim now {hub.cfg['trim_ms'][arg]:+.0f} ms")
        elif cmd == "q":
            raise KeyboardInterrupt
        else:
            print(help_)
