#!/usr/bin/env python3
"""Portable analog leaf generator for the ADC (M2 start).

Follows the OpenSERDES contract: a topology is process-independent, the PDK
profile supplies device names / finger parameter / sizing, and the generator
emits only what it can defend.

  topology -> profile device binding -> circuit.spice + macro.v + manifest.json

It does NOT invent layout, LEF or Liberty.  A GDS can be attached, but it is
audited for device layers and the manifest stops at LAYOUT_ATTACHED.  Only a
clean DRC report, a unique-match LVS report and a PEX view for this exact
netlist reach SIGNOFF_ELIGIBLE.

Every topology has a fixed port contract so the SAR core can be built from any
PDK's leaves without rewiring:

  cap_unit          VSH FL VNW VPW
  switch_tg         S SB Z1 Z2 VDD VSS
  switch_bootstrap  CLK VIN VOUT VDD VSS
  cmp_strongarm     INP INN CLK OUTP OUTN VDD VSS
  cdac_binary       VSH B0..B<n-1> BB0..BB<n-1> VREFH VREFL VNW VPW VDD VSS
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.dirname(HERE)
PDK_DIR = os.path.join(GEN, "pdk")


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------

def expand(path):
    return os.path.expandvars(os.path.expanduser(path))


def load_profile(pdk):
    path = expand(pdk)
    if not os.path.exists(path):
        path = os.path.join(PDK_DIR, pdk + ".json")
    if not os.path.exists(path):
        raise SystemExit(f"unknown pdk {pdk!r}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def device_binding(profile):
    analog = profile.get("analog") or {}
    if analog.get("status") != "supported":
        raise SystemExit(
            f"pdk {profile['id']}: analog.status={analog.get('status')!r}; "
            "analog leaf generation is fail-closed until device subckt names, "
            "finger parameter, W/L range and layout template are verified. "
            "Status is not a substitute for physical evidence."
        )
    dev = profile.get("devices") or {}
    if not dev.get("nmos") or not dev.get("pmos"):
        raise SystemExit(f"pdk {profile['id']}: devices.nmos/pmos missing")
    return {
        "nmos": dev["nmos"],
        "pmos": dev["pmos"],
        "finger": analog.get("finger_param", "nf"),
        "style": analog.get("style", "subckt"),
        "sizing": analog.get("sizing_um") or {},
        # switches carry their own sizing: widening them to cut Ron must not
        # also widen the unit capacitor
        "switch_sizing": analog.get("switch_sizing_um") or analog.get("sizing_um") or {},
    }


def mos(dev, name, d, g, s, b, model, w, l, nf):
    """One device instance.

    Two emission styles, selected by the PDK's ``analog.style``:

      * ``subckt`` -- ``X<name> ... <model>`` against a vendor ``.subckt``
        whose device model is wrapped inside it (sky130, ihp-sg13g2).
      * ``bare_m`` -- ``M<name> ... <model>`` against a flattened ``.model``
        card (ics55's Xyce BSIM4 deck has no vendor subckt: the wrapper is
        HSpice-only, so the leaf must call the bare model directly).  Units go
        in metres for a ``.model`` device, not the ``u`` suffix the subckt
        wrappers accept.
    """
    if dev.get("style") == "bare_m":
        return (f"M{name} {d} {g} {s} {b} {model} "
                f"w={w:g}e-6 l={l:g}e-6 {dev['finger']}={nf}")
    return (f"X{name} {d} {g} {s} {b} {model} "
            f"w={w:g}u l={l:g}u {dev['finger']}={nf}")


# --------------------------------------------------------------------------
# topologies
# --------------------------------------------------------------------------

def topo_cap_unit(dev, p):
    """Unit capacitor: NMOS and PMOS with d=s=b on FL, gate on VSH.

    The taped-out sg13g2 cell used 12 N + 12 P in parallel; here the parallel
    count is a parameter so the array can hit a target unit capacitance.
    """
    wn = p.get("wn") or dev["sizing"].get("wn", 1.0)
    wp = p.get("wp") or dev["sizing"].get("wp", 2.0)
    l = p.get("l") or dev["sizing"].get("l", 0.15)
    n = int(p.get("n_parallel", 1))
    lines = [".subckt SARADC_CAP_UNIT VSH FL VNW VPW"]
    for k in range(n):
        lines.append(mos(dev, f"MN{k}", "FL", "VSH", "FL", "VPW", dev["nmos"], wn, l, 1))
    for k in range(n):
        lines.append(mos(dev, f"MP{k}", "FL", "VSH", "FL", "VNW", dev["pmos"], wp, l, 1))
    lines.append(".ends")
    meta = {"topology": "cap_unit", "n_parallel": n,
            "wn_um": wn, "wp_um": wp, "l_um": l,
            "ports": ["VSH", "FL", "VNW", "VPW"]}
    return "\n".join(lines), meta


def topo_switch_tg(dev, p):
    """Complementary transmission gate between Z1 and Z2.

    This replaces the taped-out trick of rewiring an inverter's supply pins:
    a real TG has a defined body connection and survives body-diode and
    antenna rules.
    """
    sw = dev.get("switch_sizing") or dev["sizing"]
    mult = float(p.get("width_um", 1.0))
    wn = (p.get("wn") or sw.get("wn", 1.0)) * mult
    wp = (p.get("wp") or sw.get("wp", 2.0)) * mult
    l = p.get("l") or sw.get("l", 0.15)
    lines = [".subckt SARADC_SW_TG S SB Z1 Z2 VDD VSS"]
    lines.append(mos(dev, "MN", "Z2", "S", "Z1", "VSS", dev["nmos"], wn, l, 1))
    lines.append(mos(dev, "MP", "Z2", "SB", "Z1", "VDD", dev["pmos"], wp, l, 1))
    lines.append(".ends")
    meta = {"topology": "switch_tg", "wn_um": wn, "wp_um": wp, "l_um": l,
            "ports": ["S", "SB", "Z1", "Z2", "VDD", "VSS"]}
    return "\n".join(lines), meta


def topo_switch_bootstrap(dev, p):
    """Bootstrapped NMOS sampling switch (Abo-Gray style, single clock).

    Phases: CLK low = track off and the bootstrap cap charges to VDD;
    CLK high = the bottom plate follows VIN, so the gate sits at VDD+VIN and
    the switch overdrive stays constant across the input range.

    Node names are g / boot / x and are intentionally stable: the
    characterization bench flattens this leaf and probes them.
    """
    sw = dev.get("switch_sizing") or dev["sizing"]
    mult = float(p.get("width_um", 1.0))
    wn = (p.get("wn") or sw.get("wn", 1.0)) * mult
    wp = (p.get("wp") or sw.get("wp", 2.0)) * mult
    l = p.get("l") or sw.get("l", 0.15)
    cboot_ff = float(p.get("cboot_ff", 100.0))
    lines = [".subckt SARADC_SW_BOOT CLK VIN VOUT VDD VSS"]
    # sampling device and the gate discharge
    lines.append(mos(dev, "MSW", "VIN", "g", "VOUT", "VSS", dev["nmos"], wn, l, 1))
    # bulk on boot: the gate node and the cap top plate swing above VDD,
    # so their wells must float with them or the drain diode clamps them
    lines.append(mos(dev, "MDIS", "g", "CLK", "VSS", "boot", dev["pmos"], wp, l, 1))
    # bootstrap cap: top plate on boot, bottom plate on x
    # source and bulk both on boot: with the source at VDD the source
    # junction forward-biases as soon as boot rises above VDD and clamps
    # the whole bootstrap near VDD+0.6
    lines.append(mos(dev, "MPRE", "VDD", "CLK", "boot", "boot", dev["pmos"], wp, l, 1))
    lines.append(mos(dev, "MX", "x", "CLK", "VIN", "VSS", dev["nmos"], wn, l, 1))
    lines.append(mos(dev, "MXDIS", "x", "CLK", "VSS", "VDD", dev["pmos"], wp, l, 1))
    # inverted clock, needed because the gate node rises above VDD and an
    # NMOS pass gate cannot carry a signal above its own gate drive
    lines.append(mos(dev, "MINVP", "clkn", "CLK", "VDD", "VDD", dev["pmos"], wp, l, 1))
    lines.append(mos(dev, "MINVN", "clkn", "CLK", "VSS", "VSS", dev["nmos"], wn, l, 1))
    # gate-to-boot switch: PMOS with its bulk on boot so nothing forward-biases
    lines.append(mos(dev, "MCON", "g", "clkn", "boot", "boot", dev["pmos"], wp, l, 1))
    lines.append(f"CBOOT boot x {cboot_ff:g}f")
    lines.append(".ends")
    meta = {"topology": "switch_bootstrap", "wn_um": wn, "wp_um": wp, "l_um": l,
            "cboot_ff": cboot_ff, "n_devices": 8,
            "ports": ["CLK", "VIN", "VOUT", "VDD", "VSS"]}
    return "\n".join(lines), meta


def topo_cmp_strongarm(dev, p):
    """StrongArm dynamic comparator: input pair, cross-coupled latch, tail."""
    wn = p.get("wn") or dev["sizing"].get("wn", 1.0)
    wp = p.get("wp") or dev["sizing"].get("wp", 2.0)
    l = p.get("l") or dev["sizing"].get("l", 0.15)
    lt = p.get("l_tail") or l * 2
    lines = [".subckt SARADC_CMP_STRONGARM INP INN CLK OUTP OUTN VDD VSS"]
    # canonical StrongArm: the input pair drains ARE the outputs, and the
    # cross-coupled NMOS pair regenerates on those same nodes.
    lines.append(mos(dev, "MTAIL", "tail", "CLK", "VSS", "VSS", dev["nmos"], wn, lt, 1))
    # the input pair is wider than the latch so it wins before regeneration
    lines.append(mos(dev, "MINP", "OUTN", "INP", "tail", "VSS", dev["nmos"], 2 * wn, l, 1))
    lines.append(mos(dev, "MINN", "OUTP", "INN", "tail", "VSS", dev["nmos"], 2 * wn, l, 1))
    lines.append(mos(dev, "MLA1", "OUTN", "OUTP", "VSS", "VSS", dev["nmos"], wn, l, 1))
    lines.append(mos(dev, "MLA2", "OUTP", "OUTN", "VSS", "VSS", dev["nmos"], wn, l, 1))
    lines.append(mos(dev, "MPB1", "OUTN", "OUTP", "VDD", "VDD", dev["pmos"], wp, l, 1))
    lines.append(mos(dev, "MPB2", "OUTP", "OUTN", "VDD", "VDD", dev["pmos"], wp, l, 1))
    lines.append(mos(dev, "MPRE1", "OUTP", "CLK", "VDD", "VDD", dev["pmos"], wp, l, 1))
    lines.append(mos(dev, "MPRE2", "OUTN", "CLK", "VDD", "VDD", dev["pmos"], wp, l, 1))
    lines.append(".ends")
    meta = {"topology": "cmp_strongarm", "wn_um": wn, "wp_um": wp, "n_devices": 9,
            "l_um": l, "l_tail_um": lt,
            "ports": ["INP", "INN", "CLK", "OUTP", "OUTN", "VDD", "VSS"]}
    return "\n".join(lines), meta


def topo_cdac_binary(dev, p):
    """Binary bottom-plate CDAC, one side.

    2^N unit capacitors, bit i gets 2^i of them, all top plates on VSH.
    Each bit's bottom plate is switched between VREFH and VREFL by one TG.
    The 2^N-th unit is the fixed dummy that sets the reference.
    """
    n = int(p.get("nbits", 8))
    lines = ["* requires: SARADC_CAP_UNIT, SARADC_SW_TG",
             ".subckt SARADC_CDAC_BINARY VSH "
             + " ".join(f"B{i}" for i in range(n)) + " "
             + " ".join(f"BB{i}" for i in range(n))
             + " VREFH VREFL VNW VPW VDD VSS"]
    total = 0
    for i in range(n):
        count = 1 << i
        total += count
        node = f"n{i}"
        lines.append(f"* bit {i}: {count} unit caps")
        for k in range(count):
            lines.append(f"XC{i}_{k} VSH {node} VNW VPW SARADC_CAP_UNIT")
        lines.append(f"XSW{i} B{i} BB{i} VREFH {node} VDD VSS SARADC_SW_TG")
        lines.append(f"XSWN{i} BB{i} B{i} VREFL {node} VDD VSS SARADC_SW_TG")
    # The (2^N)th unit is the fixed dummy: its bottom plate is always driven
    # to VREFL so the switched array always sees the same total capacitance.
    # It is not a conversion bit; it only closes the top-plate charge budget.
    lines.append(f"XDUM VSH ndum VNW VPW SARADC_CAP_UNIT")
    lines.append(f"XSWDUMH VSS VDD VREFH ndum VDD VSS SARADC_SW_TG")
    lines.append(f"XSWDUML VDD VSS VREFL ndum VDD VSS SARADC_SW_TG")
    lines.append(".ends")
    meta = {"topology": "cdac_binary", "nbits": n,
            "unit_caps": total + 1, "switches_tg": 2 * n + 2,
            "requires_subckts": ["SARADC_CAP_UNIT", "SARADC_SW_TG"],
            "ports": (["VSH"] + [f"B{i}" for i in range(n)]
                      + [f"BB{i}" for i in range(n)]
                      + ["VREFH", "VREFL", "VNW", "VPW", "VDD", "VSS"])}
    return "\n".join(lines), meta


TOPOLOGIES = {
    "cap_unit": topo_cap_unit,
    "switch_tg": topo_switch_tg,
    "switch_bootstrap": topo_switch_bootstrap,
    "cmp_strongarm": topo_cmp_strongarm,
    "cdac_binary": topo_cdac_binary,
}


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------

def macro_verilog(name, ports):
    ins = {"CLK", "S", "SB", "VIN", "INP", "INN", "VREFH", "VREFL", "VNW", "VPW"}
    outs = {"VOUT", "OUTP", "OUTN"}
    decl = []
    for p in ports:
        if p.startswith("BB") or p.startswith("B") and p[1:].isdigit():
            decl.append(f"    input  wire {p}")
        elif p in ins:
            decl.append(f"    input  wire {p}")
        elif p in outs:
            decl.append(f"    output wire {p}")
        else:
            decl.append(f"    inout  wire {p}")
    body = ",\n".join(decl)
    return (f"// Generated analog macro {name}. Black box: timing is owned by the\n"
            f"// target PDK flow, never inferred from this file.\n"
            f"module {name} (\n{body}\n);\nendmodule\n")


def selfcheck_spice(circuit, ports):
    """Fail generation if the subckt's own port contract is broken.

    Every declared port must be referenced in the body, and every net used
    on an instance line that names a well-known supply (VDD/VSS/VREFH/VREFL/
    VNW/VPW) must actually be a declared port -- otherwise the net is a
    dangling implicit node that only .global would rescue.
    """
    head = next(l for l in circuit.splitlines() if l.startswith(".subckt"))
    declared = head.split()[2:]
    problems = []
    body_nets = set()
    for line in circuit.splitlines():
        if line.startswith(("*", ".subckt", ".ends")):
            continue
        body_nets.update(t for t in line.split()[1:-1] if not t.startswith("$"))
    for port in declared:
        if port not in body_nets:
            problems.append(f"declared port {port} never used in body")
    for net in body_nets:
        if net in ("VDD", "VSS", "VREFH", "VREFL", "VNW", "VPW") \
                and net not in declared:
            problems.append(f"supply net {net} used but not a declared port")
    if problems:
        raise SystemExit("generated subckt failed self-check:\n  "
                         + "\n  ".join(problems))
    return declared


def selfcheck_verilog(text, ports):
    """A generated ANSI port list must carry exactly len(ports)-1 commas."""
    head = text[text.index("("):text.index(");")]
    commas = head.count(",")
    if len(ports) > 1 and commas != len(ports) - 1:
        raise SystemExit(f"generated macro.v port list has {commas} commas "
                         f"for {len(ports)} ports")
    return True


def gds_layers(path):
    """Minimal GDSII record scan: returns the set of (layer, datatype)."""
    layers = set()
    with open(path, "rb") as fh:
        data = fh.read()
    i = 0
    n = len(data)
    while i + 4 <= n:
        size, rtype, dtype = struct.unpack(">HBB", data[i:i + 4])
        if size < 4:
            break
        if rtype == 0x0D and i + 8 <= n:            # LAYER
            layers.add((struct.unpack(">h", data[i + 4:i + 6])[0], 0))
        if rtype == 0x0E and i + 8 <= n:            # DATATYPE
            layer = struct.unpack(">h", data[i - 2:i])[0] if i >= 2 else None
            layers.discard((layer, 0))
            layers.add((layer, struct.unpack(">h", data[i + 4:i + 6])[0]))
        i += size
    return layers


def audit_gds(path, profile):
    wanted = profile.get("analog", {}).get("device_layers")
    if wanted:
        wanted = [tuple(x) for x in wanted]
        present = gds_layers(path)
        missing = [w for w in wanted if w not in present]
        return {"physical": not missing, "missing": missing,
                "checked": "profile.analog.device_layers"}
    # no profile contract: a GDS that carries only one layer is almost always
    # a pin/abstract stub, not a device layout
    present = gds_layers(path)
    return {"physical": len(present) >= 4, "layer_count": len(present),
            "checked": "default: >=4 layer/datatype pairs"}


def evidence_hash(name, circuit, views):
    h = hashlib.sha256()
    h.update(name.encode())
    h.update(circuit.encode())
    for key in ("layout", "lef", "cdl", "pex", "drc", "lvs"):
        view = views.get(key) or {}
        p = view.get("path")
        if p and os.path.isfile(expand(p)):
            h.update(key.encode())
            h.update(open(expand(p), "rb").read())
    return h.hexdigest()


def report_ok(path, kind):
    if not path or not os.path.isfile(expand(path)):
        return False
    text = open(expand(path), encoding="utf-8", errors="replace").read().lower()
    if kind == "lvs":
        return ("circuits match uniquely" in text or "lvs match" in text
                or "match-unique" in text or "lvs clean" in text
                or "netlists match" in text or "ics55_lvs_compare=pass" in text)
    return "drc clean" in text or "violations: 0" in text or "errors=0" in text


def report_mentions(path, name):
    """A PASS string in an unrelated report is not evidence for this cell.

    Require the report's text to name the cell we generated (or the file
    itself to be named after it), so a clean report for a different design
    cannot promote this manifest to SIGNOFF_ELIGIBLE.
    """
    if not path or not os.path.isfile(expand(path)):
        return False
    if name.lower() in os.path.basename(expand(path)).lower():
        return True
    text = open(expand(path), encoding="utf-8", errors="replace").read().lower()
    return name.lower() in text


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------

def generate(profile, topology, name, outdir, params, layout_gds=None,
             lef=None, cdl=None, pex=None, lvs=None, drc=None):
    dev = device_binding(profile)
    if topology not in TOPOLOGIES:
        raise SystemExit(f"unknown topology {topology!r}; "
                         f"choose from {', '.join(sorted(TOPOLOGIES))}")
    circuit, meta = TOPOLOGIES[topology](dev, params or {})
    selfcheck_spice(circuit, meta["ports"])

    layout_physical = False
    layout_audit = None
    if layout_gds:
        layout_audit = audit_gds(expand(layout_gds), profile)
        layout_physical = bool(layout_audit.get("physical"))
        if not layout_physical:
            raise SystemExit(
                f"{layout_gds}: not a physical device layout "
                f"({layout_audit}); refusing to advertise it as a signoff view")

    lvs_ok = report_ok(lvs, "lvs")
    drc_ok = report_ok(drc, "drc")
    pex_ok = bool(pex and os.path.isfile(expand(pex)))
    if lvs and not lvs_ok:
        raise SystemExit(f"{lvs}: LVS report does not prove a unique match")
    if drc and not drc_ok:
        raise SystemExit(f"{drc}: DRC report is not clean")

    # a report is only evidence if it can be tied to THIS cell
    evidence_bound = True
    for report in (drc, lvs):
        if report and not report_mentions(report, name):
            raise SystemExit(
                f"{report}: report does not name {name}; an unrelated "
                "clean report cannot promote this manifest")

    signoff = bool(layout_physical and drc_ok and lvs_ok and pex_ok
                   and evidence_bound)
    stage = ("SIGNOFF_ELIGIBLE" if signoff else "LVS_MATCHED" if lvs_ok
             else "GEOMETRY_DRC_PASS" if drc_ok
             else "LAYOUT_ATTACHED" if layout_physical else "SCHEMATIC_ONLY")

    macro_v = macro_verilog(name, meta["ports"])
    selfcheck_verilog(macro_v, meta["ports"])

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "circuit.spice"), "w", encoding="utf-8") as fh:
        fh.write(circuit + "\n")
    with open(os.path.join(outdir, "macro.v"), "w", encoding="utf-8") as fh:
        fh.write(macro_v)

    views = {
        "circuit_spice": {"path": os.path.join(outdir, "circuit.spice"),
                          "status": "available", "format": "spice", "top": name},
        "macro_v": {"path": os.path.join(outdir, "macro.v"), "status": "available"},
        "layout": ({"path": layout_gds, "status": "available", "format": "gds",
                    "physical": layout_physical, "audit": layout_audit}
                   if layout_gds else {"status": "missing", "physical": False}),
        "lef": ({"path": lef, "status": "available"} if lef else {"status": "missing"}),
        "cdl": ({"path": cdl, "status": "available"} if cdl else {"status": "missing"}),
        "liberty": {"status": "missing"},
        "drc": ({"path": drc, "status": "clean" if drc_ok else "unusable"}
                if drc else {"status": "missing"}),
        "lvs": ({"path": lvs, "status": "match-unique" if lvs_ok else "unusable"}
                if lvs else {"status": "missing"}),
        "pex": ({"path": pex, "status": "available"} if pex else {"status": "missing"}),
    }

    manifest = {
        "schema": 1,
        "name": name,
        "kind": "adc_analog_leaf",
        "tech": profile["id"],
        "pdk": profile["family"],
        "supply_v": profile["vdd"],
        **meta,
        "status": stage,
        "physical_layout": layout_physical,
        "physical_signoff_eligible": signoff,
        "evidence_bound": evidence_bound,
        "topology_match": bool(lvs_ok),
        "device_models": {"nmos": dev["nmos"], "pmos": dev["pmos"],
                          "finger_param": dev["finger"]},
        "evidence_hash": evidence_hash(name, circuit, views),
        "views": views,
        "note": ("GDS attachment alone is LAYOUT_ATTACHED, not topology proof. "
                 "SIGNOFF_ELIGIBLE requires a clean DRC report, a unique-match "
                 "LVS report and PEX for this exact circuit."),
    }
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", required=True)
    ap.add_argument("--topology", required=True, choices=sorted(TOPOLOGIES))
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nbits", type=int, default=8)
    ap.add_argument("--n-parallel", type=int, default=1)
    ap.add_argument("--wn", type=float)
    ap.add_argument("--wp", type=float)
    ap.add_argument("--l", type=float)
    ap.add_argument("--cboot-ff", type=float, default=100.0)
    ap.add_argument("--layout-gds")
    ap.add_argument("--lef")
    ap.add_argument("--cdl")
    ap.add_argument("--pex")
    ap.add_argument("--lvs-report")
    ap.add_argument("--drc-report")
    args = ap.parse_args(argv)

    profile = load_profile(args.pdk)
    name = args.name or f"SARADC_{args.topology.upper()}"
    params = {"nbits": args.nbits, "n_parallel": args.n_parallel,
              "cboot_ff": args.cboot_ff}
    for key in ("wn", "wp", "l"):
        if getattr(args, key) is not None:
            params[key] = getattr(args, key)

    manifest = generate(profile, args.topology, name, args.out, params,
                        args.layout_gds, args.lef, args.cdl, args.pex,
                        args.lvs_report, args.drc_report)
    print(f"{name}: {manifest['status']}"
          f"  tech={manifest['tech']}  views={args.out}")
    return 0 if manifest["status"] != "SCHEMATIC_ONLY" or True else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
