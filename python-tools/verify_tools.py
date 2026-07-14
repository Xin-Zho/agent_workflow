"""
科学计算验证工具 — 量纲检查 + 数量级检查 + 回代验证 + 知识交叉验证

所有函数为同步纯函数，由 tools.py 中的 async wrapper 调用。
"""

import math
import re
from typing import Any

import scipy.constants as _const

# sympy 可选：回代验证需要
try:
    import sympy as _sympy
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )
    _SYMPY_OK = True
except ImportError:
    _SYMPY_OK = False

# ── sympy 安全解析（复用 tools.py 的安全策略）─────────────────────

_SYM_NAMES = {
    'x', 'y', 'z', 't', 'a', 'b', 'c', 'n', 'm', 'k', 'h', 'r', 'T', 'P', 'V',
    'theta', 'alpha', 'beta', 'gamma', 'omega', 'lambda_', 'sigma',
    'E', 'F', 'U',
}
_SYM_TABLE = {name: _sympy.symbols(name) for name in _SYM_NAMES}

_BLOCKED_NAMES = frozenset({
    '__import__', 'eval', 'exec', 'open', 'compile', 'getattr',
    'setattr', 'delattr', 'globals', 'locals', '__builtins__',
    '__builtin__', '__class__', '__bases__', '__subclasses__',
    'os', 'sys', 'subprocess', 'importlib', 'builtins',
    'pty', 'posix', 'shutil', 'socket', 'ctypes',
})

_TRANSFORMATIONS = standard_transformations + (implicit_multiplication_application, convert_xor)


def _safe_sympy(expr_str: str) -> _sympy.Expr:
    """安全解析 sympy 表达式。"""
    if not _SYMPY_OK:
        raise RuntimeError("sympy not available")
    tokens = set(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', expr_str))
    blocked = tokens & _BLOCKED_NAMES
    if blocked:
        raise ValueError(f"Blocked identifiers: {', '.join(sorted(blocked))}")
    local_dict = {
        'diff': _sympy.diff, 'integrate': _sympy.integrate, 'solve': _sympy.solve,
        'dsolve': _sympy.dsolve, 'limit': _sympy.limit, 'series': _sympy.series,
        'Matrix': _sympy.Matrix, 'pi': _sympy.pi, 'E': _sympy.E,
        'sin': _sympy.sin, 'cos': _sympy.cos, 'tan': _sympy.tan,
        'log': _sympy.log, 'exp': _sympy.exp, 'sqrt': _sympy.sqrt,
        'simplify': _sympy.simplify, 'expand': _sympy.expand, 'factor': _sympy.factor,
        'Eq': _sympy.Eq, 'symbols': _sympy.symbols,
    }
    local_dict.update(_SYM_TABLE)
    return parse_expr(expr_str, local_dict=local_dict, transformations=_TRANSFORMATIONS)


# ═══════════════════════════════════════════════════════════════════════════
# 1. 量纲检查 — verify_dimensional
# ═══════════════════════════════════════════════════════════════════════════

# 常见物理/化学量 → SI 基本量纲 (length=L, mass=M, time=T, current=I,
#   temperature=Θ, amount=N, luminous=J)
# 用 tuple (L, M, T, I, Θ, N, J) 表示
_SI_DIMENSIONS = {
    "length":               (1, 0, 0, 0, 0, 0, 0),
    "distance":             (1, 0, 0, 0, 0, 0, 0),
    "displacement":         (1, 0, 0, 0, 0, 0, 0),
    "radius":               (1, 0, 0, 0, 0, 0, 0),
    "wavelength":           (1, 0, 0, 0, 0, 0, 0),
    "area":                 (2, 0, 0, 0, 0, 0, 0),
    "volume":               (3, 0, 0, 0, 0, 0, 0),
    "mass":                 (0, 1, 0, 0, 0, 0, 0),
    "time":                 (0, 0, 1, 0, 0, 0, 0),
    "period":               (0, 0, 1, 0, 0, 0, 0),
    "frequency":            (0, 0, -1, 0, 0, 0, 0),
    "velocity":             (1, 0, -1, 0, 0, 0, 0),
    "speed":                (1, 0, -1, 0, 0, 0, 0),
    "acceleration":         (1, 0, -2, 0, 0, 0, 0),
    "force":                (1, 1, -2, 0, 0, 0, 0),
    "weight":               (1, 1, -2, 0, 0, 0, 0),
    "energy":               (2, 1, -2, 0, 0, 0, 0),
    "work":                 (2, 1, -2, 0, 0, 0, 0),
    "heat":                 (2, 1, -2, 0, 0, 0, 0),
    "power":                (2, 1, -3, 0, 0, 0, 0),
    "pressure":             (-1, 1, -2, 0, 0, 0, 0),
    "stress":               (-1, 1, -2, 0, 0, 0, 0),
    "density":              (-3, 1, 0, 0, 0, 0, 0),
    "concentration":        (-3, 0, 0, 0, 0, 1, 0),  # mol/m^3
    "molar_mass":           (0, 1, 0, 0, 0, -1, 0),
    "momentum":             (1, 1, -1, 0, 0, 0, 0),
    "impulse":              (1, 1, -1, 0, 0, 0, 0),
    "angular_momentum":     (2, 1, -1, 0, 0, 0, 0),
    "torque":               (2, 1, -2, 0, 0, 0, 0),
    "moment_of_inertia":    (2, 1, 0, 0, 0, 0, 0),
    "electric_charge":      (0, 0, 1, 1, 0, 0, 0),
    "current":              (0, 0, 0, 1, 0, 0, 0),
    "voltage":              (2, 1, -3, -1, 0, 0, 0),
    "electric_potential":   (2, 1, -3, -1, 0, 0, 0),
    "electric_field":       (1, 1, -3, -1, 0, 0, 0),
    "magnetic_field":       (0, 1, -2, -1, 0, 0, 0),
    "magnetic_flux":        (2, 1, -2, -1, 0, 0, 0),
    "resistance":           (2, 1, -3, -2, 0, 0, 0),
    "capacitance":          (-2, -1, 4, 2, 0, 0, 0),
    "inductance":           (2, 1, -2, -2, 0, 0, 0),
    "temperature":          (0, 0, 0, 0, 1, 0, 0),
    "entropy":              (2, 1, -2, 0, -1, 0, 0),
    "heat_capacity":        (2, 1, -2, 0, -1, 0, 0),
    "specific_heat":        (2, 0, -2, 0, -1, 0, 0),
    "thermal_conductivity": (1, 1, -3, 0, -1, 0, 0),
    "amount_of_substance":  (0, 0, 0, 0, 0, 1, 0),
    "ph":                   (0, 0, 0, 0, 0, 0, 0),  # dimensionless
    "dimensionless":        (0, 0, 0, 0, 0, 0, 0),
    "angle":                (0, 0, 0, 0, 0, 0, 0),
    "refractive_index":     (0, 0, 0, 0, 0, 0, 0),
    # Common derived units shorthand
    "meter":                (1, 0, 0, 0, 0, 0, 0),
    "kilogram":             (0, 1, 0, 0, 0, 0, 0),
    "second":               (0, 0, 1, 0, 0, 0, 0),
    "newton":               (1, 1, -2, 0, 0, 0, 0),
    "joule":                (2, 1, -2, 0, 0, 0, 0),
    "watt":                 (2, 1, -3, 0, 0, 0, 0),
    "pascal":               (-1, 1, -2, 0, 0, 0, 0),
    "coulomb":              (0, 0, 1, 1, 0, 0, 0),
    "volt":                 (2, 1, -3, -1, 0, 0, 0),
    "ohm":                  (2, 1, -3, -2, 0, 0, 0),
    "farad":                (-2, -1, 4, 2, 0, 0, 0),
    "henry":                (2, 1, -2, -2, 0, 0, 0),
    "tesla":                (0, 1, -2, -1, 0, 0, 0),
    "weber":                (2, 1, -2, -1, 0, 0, 0),
    "hertz":                (0, 0, -1, 0, 0, 0, 0),
}

_DIM_NAMES = ["L", "M", "T", "I", "Θ", "N", "J"]


def _dim_to_str(dim: tuple) -> str:
    """将量纲 tuple 转为可读字符串，如 (1,1,-2,0,0,0,0) → 'L·M·T⁻²'"""
    parts = []
    for exp, name in zip(dim, _DIM_NAMES):
        if exp == 0:
            continue
        if exp == 1:
            parts.append(name)
        else:
            sup = str(exp).replace("-", "⁻")
            for old, new in [("0", "⁰"), ("1", "¹"), ("2", "²"), ("3", "³"),
                             ("4", "⁴"), ("5", "⁵"), ("6", "⁶"), ("7", "⁷"),
                             ("8", "⁸"), ("9", "⁹")]:
                sup = sup.replace(old, new)
            parts.append(f"{name}{sup}")
    return "·".join(parts) if parts else "1 (dimensionless)"


def verify_dimensional(value: float, unit: str, expected_quantity: str) -> dict:
    """量纲检查：验证结果的单位是否与预期物理量匹配。

    Args:
        value: 数值
        unit: 结果单位（如 "m/s", "J", "N·m", "eV"）
        expected_quantity: 期望的物理量类型（如 "energy", "force", "velocity"）

    Returns:
        {"status": "pass"/"fail", "expected_dim": str, "actual_unit": str,
         "explanation": str}
    """
    expected = expected_quantity.lower().replace(" ", "_").replace("-", "_")
    dim = _SI_DIMENSIONS.get(expected)

    if dim is None:
        # 尝试模糊匹配
        for key in _SI_DIMENSIONS:
            if key in expected or expected in key:
                dim = _SI_DIMENSIONS[key]
                expected = key
                break

    if dim is None:
        return {
            "status": "unknown",
            "expected_quantity": expected_quantity,
            "actual_value": value,
            "actual_unit": unit,
            "explanation": f"未识别的物理量类型 '{expected_quantity}'。支持的类型：{', '.join(sorted(_SI_DIMENSIONS.keys()))}",
        }

    dim_str = _dim_to_str(dim)

    # 尝试从单位字符串推断实际量纲
    actual_dim = _parse_unit_dimension(unit)
    if actual_dim is None:
        # 无法解析单位，但仍在已知量纲表中
        return {
            "status": "pass" if dim[0] == 0 and dim[1] == 0 and dim[2] == 0 else "warning",
            "expected_quantity": expected_quantity,
            "expected_dimension": dim_str,
            "actual_value": value,
            "actual_unit": unit,
            "explanation": f"期望量纲 [{dim_str}]（{expected_quantity}），实际单位 '{unit}'。无法自动解析单位量纲，请人工确认。",
        }

    if actual_dim == dim:
        return {
            "status": "pass",
            "expected_quantity": expected_quantity,
            "expected_dimension": dim_str,
            "actual_value": value,
            "actual_unit": unit,
            "actual_dimension": _dim_to_str(actual_dim),
            "explanation": f"✓ 量纲匹配：[{dim_str}]（{expected_quantity}），单位 '{unit}' 的量纲为 [{_dim_to_str(actual_dim)}]。",
        }
    else:
        return {
            "status": "fail",
            "expected_quantity": expected_quantity,
            "expected_dimension": dim_str,
            "actual_value": value,
            "actual_unit": unit,
            "actual_dimension": _dim_to_str(actual_dim),
            "explanation": f"✗ 量纲不匹配：期望 [{dim_str}]（{expected_quantity}），但单位 '{unit}' 的量纲为 [{_dim_to_str(actual_dim)}]。请检查计算过程。",
        }


# 常见单位 → SI 量纲映射
_UNIT_DIM_MAP = {
    # Length
    "m": (1, 0, 0, 0, 0, 0, 0), "meter": (1, 0, 0, 0, 0, 0, 0), "metre": (1, 0, 0, 0, 0, 0, 0),
    "km": (1, 0, 0, 0, 0, 0, 0), "cm": (1, 0, 0, 0, 0, 0, 0), "mm": (1, 0, 0, 0, 0, 0, 0),
    "nm": (1, 0, 0, 0, 0, 0, 0), "pm": (1, 0, 0, 0, 0, 0, 0), "Å": (1, 0, 0, 0, 0, 0, 0),
    "angstrom": (1, 0, 0, 0, 0, 0, 0), "ft": (1, 0, 0, 0, 0, 0, 0), "inch": (1, 0, 0, 0, 0, 0, 0),
    "mile": (1, 0, 0, 0, 0, 0, 0),
    # Area
    "m^2": (2, 0, 0, 0, 0, 0, 0), "m²": (2, 0, 0, 0, 0, 0, 0),
    # Volume
    "m^3": (3, 0, 0, 0, 0, 0, 0), "L": (3, 0, 0, 0, 0, 0, 0), "liter": (3, 0, 0, 0, 0, 0, 0),
    "mL": (3, 0, 0, 0, 0, 0, 0), "cm^3": (3, 0, 0, 0, 0, 0, 0),
    # Mass
    "g": (0, 1, 0, 0, 0, 0, 0), "kg": (0, 1, 0, 0, 0, 0, 0),
    "mg": (0, 1, 0, 0, 0, 0, 0), "u": (0, 1, 0, 0, 0, 0, 0),
    "amu": (0, 1, 0, 0, 0, 0, 0), "Da": (0, 1, 0, 0, 0, 0, 0),
    # Time
    "s": (0, 0, 1, 0, 0, 0, 0), "sec": (0, 0, 1, 0, 0, 0, 0),
    "min": (0, 0, 1, 0, 0, 0, 0), "h": (0, 0, 1, 0, 0, 0, 0),
    "hr": (0, 0, 1, 0, 0, 0, 0), "day": (0, 0, 1, 0, 0, 0, 0),
    "yr": (0, 0, 1, 0, 0, 0, 0),
    # Frequency
    "Hz": (0, 0, -1, 0, 0, 0, 0), "kHz": (0, 0, -1, 0, 0, 0, 0),
    "MHz": (0, 0, -1, 0, 0, 0, 0), "GHz": (0, 0, -1, 0, 0, 0, 0),
    # Velocity
    "m/s": (1, 0, -1, 0, 0, 0, 0), "km/h": (1, 0, -1, 0, 0, 0, 0),
    "km/s": (1, 0, -1, 0, 0, 0, 0), "cm/s": (1, 0, -1, 0, 0, 0, 0),
    "mph": (1, 0, -1, 0, 0, 0, 0),
    # Acceleration
    "m/s^2": (1, 0, -2, 0, 0, 0, 0), "m/s²": (1, 0, -2, 0, 0, 0, 0),
    "cm/s^2": (1, 0, -2, 0, 0, 0, 0), "gal": (1, 0, -2, 0, 0, 0, 0),
    # Force
    "N": (1, 1, -2, 0, 0, 0, 0), "newton": (1, 1, -2, 0, 0, 0, 0),
    "dyn": (1, 1, -2, 0, 0, 0, 0), "dyne": (1, 1, -2, 0, 0, 0, 0),
    "lbf": (1, 1, -2, 0, 0, 0, 0),
    # Energy
    "J": (2, 1, -2, 0, 0, 0, 0), "joule": (2, 1, -2, 0, 0, 0, 0),
    "kJ": (2, 1, -2, 0, 0, 0, 0), "MJ": (2, 1, -2, 0, 0, 0, 0),
    "eV": (2, 1, -2, 0, 0, 0, 0), "keV": (2, 1, -2, 0, 0, 0, 0),
    "MeV": (2, 1, -2, 0, 0, 0, 0), "GeV": (2, 1, -2, 0, 0, 0, 0),
    "erg": (2, 1, -2, 0, 0, 0, 0), "cal": (2, 1, -2, 0, 0, 0, 0),
    "kcal": (2, 1, -2, 0, 0, 0, 0), "kJ/mol": (2, 1, -2, 0, 0, -1, 0),
    "kcal/mol": (2, 1, -2, 0, 0, -1, 0),
    "hartree": (2, 1, -2, 0, 0, 0, 0), "Ha": (2, 1, -2, 0, 0, 0, 0),
    "rydberg": (2, 1, -2, 0, 0, 0, 0),
    # Power
    "W": (2, 1, -3, 0, 0, 0, 0), "kW": (2, 1, -3, 0, 0, 0, 0),
    "MW": (2, 1, -3, 0, 0, 0, 0), "hp": (2, 1, -3, 0, 0, 0, 0),
    # Pressure
    "Pa": (-1, 1, -2, 0, 0, 0, 0), "kPa": (-1, 1, -2, 0, 0, 0, 0),
    "MPa": (-1, 1, -2, 0, 0, 0, 0), "GPa": (-1, 1, -2, 0, 0, 0, 0),
    "atm": (-1, 1, -2, 0, 0, 0, 0), "bar": (-1, 1, -2, 0, 0, 0, 0),
    "torr": (-1, 1, -2, 0, 0, 0, 0), "mmHg": (-1, 1, -2, 0, 0, 0, 0),
    "psi": (-1, 1, -2, 0, 0, 0, 0),
    # Electric
    "C": (0, 0, 1, 1, 0, 0, 0), "coulomb": (0, 0, 1, 1, 0, 0, 0),
    "A": (0, 0, 0, 1, 0, 0, 0), "ampere": (0, 0, 0, 1, 0, 0, 0),
    "mA": (0, 0, 0, 1, 0, 0, 0), "μA": (0, 0, 0, 1, 0, 0, 0),
    "V": (2, 1, -3, -1, 0, 0, 0), "volt": (2, 1, -3, -1, 0, 0, 0),
    "kV": (2, 1, -3, -1, 0, 0, 0), "mV": (2, 1, -3, -1, 0, 0, 0),
    "Ω": (2, 1, -3, -2, 0, 0, 0), "ohm": (2, 1, -3, -2, 0, 0, 0),
    "F": (-2, -1, 4, 2, 0, 0, 0), "farad": (-2, -1, 4, 2, 0, 0, 0),
    "H": (2, 1, -2, -2, 0, 0, 0), "henry": (2, 1, -2, -2, 0, 0, 0),
    "T": (0, 1, -2, -1, 0, 0, 0), "tesla": (0, 1, -2, -1, 0, 0, 0),
    "Wb": (2, 1, -2, -1, 0, 0, 0), "weber": (2, 1, -2, -1, 0, 0, 0),
    "e": (0, 0, 1, 1, 0, 0, 0),  # elementary charge
    # Temperature
    "K": (0, 0, 0, 0, 1, 0, 0), "kelvin": (0, 0, 0, 0, 1, 0, 0),
    "°C": (0, 0, 0, 0, 1, 0, 0), "°F": (0, 0, 0, 0, 1, 0, 0),
    # Amount
    "mol": (0, 0, 0, 0, 0, 1, 0), "mole": (0, 0, 0, 0, 0, 1, 0),
    "mmol": (0, 0, 0, 0, 0, 1, 0),
    # Density & concentration
    "kg/m^3": (-3, 1, 0, 0, 0, 0, 0), "kg/m³": (-3, 1, 0, 0, 0, 0, 0),
    "g/cm^3": (-3, 1, 0, 0, 0, 0, 0), "g/cm³": (-3, 1, 0, 0, 0, 0, 0),
    "g/mL": (-3, 1, 0, 0, 0, 0, 0),
    "mol/L": (-3, 0, 0, 0, 0, 1, 0), "M": (-3, 0, 0, 0, 0, 1, 0),
    "mol/m^3": (-3, 0, 0, 0, 0, 1, 0),
    "g/L": (-3, 1, 0, 0, 0, 0, 0),
    # Dimensionless
    "": (0, 0, 0, 0, 0, 0, 0),
    "dimensionless": (0, 0, 0, 0, 0, 0, 0),
    "%": (0, 0, 0, 0, 0, 0, 0),
}


def _parse_unit_dimension(unit: str) -> tuple | None:
    """解析单位字符串为 SI 量纲 tuple。如 'N·m' → (2,1,-2,0,0,0,0)。"""
    if not unit or not unit.strip():
        return (0, 0, 0, 0, 0, 0, 0)

    unit = unit.strip()

    # 直接查表
    if unit in _UNIT_DIM_MAP:
        return _UNIT_DIM_MAP[unit]

    # 处理复合单位 "N·m", "J/s", "kg*m/s^2", "m s^-1" 等
    # 分割：·, *, /, 空格
    # 先按 / 分开分子分母
    if "/" in unit:
        parts = unit.split("/", 1)
        num_dim = _parse_unit_dimension(parts[0])
        if num_dim is None:
            return None
        denom_parts = re.findall(r'([A-Za-zμΩÅ°%]+(?:\^?\d*)?)', parts[1])
        denom_dim = (0, 0, 0, 0, 0, 0, 0)
        for dp in denom_parts:
            d = _parse_single_unit(dp)
            if d is None:
                return None
            denom_dim = tuple(a + b for a, b in zip(denom_dim, d))
        return tuple(a - b for a, b in zip(num_dim, denom_dim))

    # 分子部分按 · * 空格 分割，各分量相加
    parts = re.split(r'[·*\s]+', unit)
    parts = [p for p in parts if p]  # remove empty
    result = (0, 0, 0, 0, 0, 0, 0)
    for part in parts:
        d = _parse_single_unit(part)
        if d is None:
            return None
        result = tuple(a + min(b, 0) if b < 0 else a + b for a, b in zip(result, d))
    return result


def _parse_single_unit(token: str) -> tuple | None:
    """解析单个单位（带可选指数），如 'm^2', 's^-1', 'N'。"""
    token = token.strip()
    if not token:
        return (0, 0, 0, 0, 0, 0, 0)

    # 分离单位名和指数：'m^2' → ('m', 2), 's^-1' → ('s', -1), 'kg' → ('kg', 1)
    m = re.match(r'^([A-Za-zμΩÅ°%]+)(?:\^(-?\d+))?$', token)
    if not m:
        return None

    unit_name = m.group(1)
    exp_str = m.group(2)
    exp = int(exp_str) if exp_str else 1

    base = _UNIT_DIM_MAP.get(unit_name)
    if base is None:
        # 尝试去前缀：k, c, m, μ, n, p, M, G
        for prefix, _ in [("k", ""), ("c", ""), ("m", ""), ("μ", ""), ("u", ""),
                          ("n", ""), ("p", ""), ("M", ""), ("G", "")]:
            if unit_name.startswith(prefix) and len(unit_name) > len(prefix):
                stripped = unit_name[len(prefix):]
                base = _UNIT_DIM_MAP.get(stripped)
                if base is not None:
                    break
        if base is None:
            return None

    return tuple(e * exp for e in base)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 数量级检查 — verify_magnitude
# ═══════════════════════════════════════════════════════════════════════════

# 常见物理量参考范围 (SI 单位)
_MAGNITUDE_REFS = {
    # ── 原子/分子尺度 ──
    "atomic_radius":        (0.3e-10, 3e-10, "m"),      # 0.3-3 Å
    "covalent_radius":      (0.3e-10, 2e-10, "m"),
    "ionic_radius":         (0.5e-10, 2.5e-10, "m"),
    "bond_length":          (0.5e-10, 3e-10, "m"),       # 0.5-3 Å
    "bond_energy":          (100e3, 1000e3, "J/mol"),    # 100-1000 kJ/mol
    "bond_angle":           (60, 180, "°"),
    "ionization_energy":    (3, 25, "eV"),
    "electron_affinity":    (-3, 3, "eV"),
    "electronegativity":    (0.7, 4.0, "Pauling"),
    "lattice_constant":     (2e-10, 10e-10, "m"),
    "lattice_energy":       (500e3, 5000e3, "J/mol"),

    # ── 分子尺度 ──
    "molecular_mass":       (1, 1e6, "g/mol"),
    "molar_mass":           (1, 1e6, "g/mol"),
    "density_solid":        (0.5, 25, "g/cm³"),
    "density_liquid":       (0.5, 15, "g/cm³"),
    "density_gas_stp":      (0.0001, 0.01, "g/cm³"),
    "melting_point":        (1, 4000, "K"),
    "boiling_point":        (1, 6000, "K"),
    "specific_heat_capacity": (100, 5000, "J/(kg·K)"),
    "thermal_conductivity": (0.01, 500, "W/(m·K)"),
    "electrical_conductivity": (1e-8, 1e8, "S/m"),
    "resistivity":          (1e-8, 1e8, "Ω·m"),

    # ── 热力学 ──
    "room_temperature":     (273, 373, "K"),
    "standard_temperature": (273.15, 273.15, "K"),
    "standard_pressure":    (101325, 101325, "Pa"),
    "atmospheric_pressure": (90000, 110000, "Pa"),
    "avogadro_number":      (6.022e23, 6.022e23, "mol⁻¹"),
    "gas_constant":         (8.314, 8.314, "J/(mol·K)"),
    "boltzmann_constant":   (1.38e-23, 1.38e-23, "J/K"),

    # ── 电磁学 ──
    "elementary_charge":    (1.602e-19, 1.602e-19, "C"),
    "electron_mass":        (9.109e-31, 9.109e-31, "kg"),
    "proton_mass":          (1.673e-27, 1.673e-27, "kg"),
    "permittivity_vacuum":  (8.85e-12, 8.85e-12, "F/m"),
    "permeability_vacuum":  (1.257e-6, 1.257e-6, "N/A²"),
    "speed_of_light":       (2.998e8, 2.998e8, "m/s"),

    # ── 量子力学 ──
    "planck_constant":      (6.626e-34, 6.626e-34, "J·s"),
    "reduced_planck":       (1.055e-34, 1.055e-34, "J·s"),
    "bohr_radius":          (5.292e-11, 5.292e-11, "m"),
    "rydberg_constant":     (1.097e7, 1.097e7, "m⁻¹"),
    "hydrogen_ground_state": (-13.6, -13.6, "eV"),
    "fine_structure":       (1/137, 1/137, ""),

    # ── 宏观物理 ──
    "gravitational_acceleration": (9.7, 9.9, "m/s²"),
    "earth_mass":           (5.97e24, 5.97e24, "kg"),
    "earth_radius":         (6.37e6, 6.37e6, "m"),
    "gravitational_constant": (6.674e-11, 6.674e-11, "N·m²/kg²"),
    "stefan_boltzmann":     (5.67e-8, 5.67e-8, "W/(m²·K⁴)"),

    # ── 化学 ──
    "ph_acid":              (0, 6.9, ""),
    "ph_base":              (7.1, 14, ""),
    "ph_neutral":           (6.5, 7.5, ""),
    "reaction_rate_constant": (1e-10, 1e10, "various"),
    "equilibrium_constant": (1e-30, 1e30, ""),
    "activation_energy":    (10e3, 500e3, "J/mol"),
    "standard_potential":   (-3.5, 3.5, "V"),
}

# 宽松因子：对于范围值，扩展此倍数作为容差
_LOOSE_FACTOR = 3.0


def verify_magnitude(value: float, quantity_type: str, unit: str = "") -> dict:
    """数量级检查：判断数值是否在已知参考范围内。

    Args:
        value: 待检查的数值
        quantity_type: 物理量类型（如 "atomic_radius", "bond_energy", "ph_acid"）
        unit: 值的单位（可选）

    Returns:
        {"status": "pass"/"fail"/"warning"/"unknown",
         "reference_range": [lo, hi, unit],
         "explanation": str}
    """
    key = quantity_type.lower().replace(" ", "_").replace("-", "_")
    ref = _MAGNITUDE_REFS.get(key)

    if ref is None:
        # 模糊匹配
        for rkey in _MAGNITUDE_REFS:
            if key in rkey or rkey in key:
                ref = _MAGNITUDE_REFS[rkey]
                break
        if ref is None:
            return {
                "status": "unknown",
                "value": value,
                "quantity_type": quantity_type,
                "unit": unit,
                "explanation": f"未找到 '{quantity_type}' 的参考范围。已知类型：{', '.join(sorted(_MAGNITUDE_REFS.keys()))}",
            }

    lo, hi, ref_unit = ref

    if lo == hi:
        # 精确常数
        if math.isclose(value, lo, rel_tol=1e-6, abs_tol=1e-9):
            return {
                "status": "pass",
                "value": value,
                "quantity_type": quantity_type,
                "unit": unit,
                "reference": f"{lo} {ref_unit}",
                "explanation": f"✓ 与已知常数 {lo} {ref_unit} 完全一致。",
            }
        else:
            rel_err = abs(value - lo) / max(abs(lo), 1e-30)
            return {
                "status": "fail" if rel_err > 0.01 else "warning",
                "value": value,
                "quantity_type": quantity_type,
                "unit": unit,
                "reference": f"{lo} {ref_unit}",
                "explanation": f"{'✗' if rel_err > 0.01 else '⚠'} 已知常数为 {lo} {ref_unit}，偏差 {rel_err:.2%}。",
            }

    # 范围值：混合扩展策略
    # 窄范围（跨距 < 10x 或 lo=0）用加法扩展，宽范围用乘法扩展
    if lo == 0 and hi > 0:
        # lo=0 的特殊情况（如 pH, 浓度等）
        expanded_lo = 0
        expanded_hi = hi * 2.0 if hi > 10 else hi + abs(hi) * 1.5
    elif lo < 0 and hi > 0:
        # 跨零范围
        pad = (hi - lo) * 1.5
        expanded_lo = lo - pad
        expanded_hi = hi + pad
    elif lo > 0 and hi > 0 and hi / lo < 10:
        pad = (hi - lo) * 2.0
        expanded_lo = max(0, lo - pad)
        expanded_hi = hi + pad
    else:
        expanded_lo = lo / _LOOSE_FACTOR if lo > 0 else lo * _LOOSE_FACTOR
        expanded_hi = hi * _LOOSE_FACTOR if hi > 0 else hi / _LOOSE_FACTOR

    if expanded_lo <= value <= expanded_hi:
        return {
            "status": "pass",
            "value": value,
            "quantity_type": quantity_type,
            "unit": unit,
            "reference_min": lo,
            "reference_max": hi,
            "reference_unit": ref_unit,
            "explanation": f"✓ 值 {value} {unit} 在参考范围 [{lo}, {hi}] {ref_unit} 内。",
        }
    else:
        # 判断差多少数量级
        mid = (lo + hi) / 2 if (lo + hi) != 0 else 1e-30
        orders_off = math.log10(abs(value / mid)) if mid != 0 and value != 0 else 0
        return {
            "status": "fail",
            "value": value,
            "quantity_type": quantity_type,
            "unit": unit,
            "reference_min": lo,
            "reference_max": hi,
            "reference_unit": ref_unit,
            "explanation": (
                f"✗ 值 {value} {unit} 偏离参考范围 [{lo}, {hi}] {ref_unit}，"
                f"偏差约 {abs(orders_off):.1f} 个数量级。强烈建议重新计算。"
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# 3. 符号回代 — verify_back_substitute
# ═══════════════════════════════════════════════════════════════════════════

def verify_back_substitute(equation: str, solution: str) -> dict:
    """回代验证：将解代回原方程/表达式，检查是否恒成立。

    支持两种输入格式：
    1. 方程形式 "x^2 - 4 = 0" + 解 "x = 2" → 检查 2²-4 是否为 0
    2. 表达式形式 "diff(x^3, x)" + 结果 "3*x^2" → 简化验证

    Args:
        equation: 原方程或表达式（sympy 语法）
        solution: 声明的解，如 "x = 2", "y = x^2 + 1", "3*x^2"

    Returns:
        {"status": "pass"/"fail"/"error", "residual": str, "explanation": str}
    """
    if not _SYMPY_OK:
        return {"status": "error", "error": "sympy not available"}

    try:
        eq_expr = _safe_sympy(equation)

        # 解析解：可能是 "var = expr" 或直接的表达式
        var = None
        sol_expr = None

        if "=" in solution and not solution.strip().startswith("="):
            # 形如 "x = 2" 或 "x = y^2 + 1"
            lhs, rhs = solution.split("=", 1)
            var = _safe_sympy(lhs.strip())
            sol_expr = _safe_sympy(rhs.strip())
        else:
            # 直接表达式结果，与 equation 比较
            sol_expr = _safe_sympy(solution)
            # 尝试从 equation 中提取变量
            if isinstance(eq_expr, _sympy.Equality):
                var = eq_expr.lhs.free_symbols
        try:
            # 情况1：equation 是等式（Eq或含有=号解析为等式）
            if isinstance(eq_expr, _sympy.Equality):
                eq_expr = eq_expr.lhs - eq_expr.rhs

            # 情况2：equation 被解析为关系式
            if isinstance(eq_expr, _sympy.Rel):
                eq_expr = eq_expr.lhs - eq_expr.rhs

            # 如果解是 x = val 形式，回代
            if var is not None:
                residual = _sympy.simplify(eq_expr.subs(var, sol_expr))
            else:
                # 解是表达式，方程是表达式 → 检查相等
                residual = _sympy.simplify(eq_expr - sol_expr)

            is_zero = False
            if residual.is_number:
                is_zero = abs(float(residual.evalf())) < 1e-10
            elif residual == 0:
                is_zero = True
            else:
                # 尝试数值评估
                try:
                    val = float(residual.evalf())
                    is_zero = abs(val) < 1e-10
                except Exception:
                    is_zero = False

            if is_zero:
                return {
                    "status": "pass",
                    "equation": equation,
                    "solution": solution,
                    "residual": str(residual),
                    "explanation": "✓ 回代验证通过：解代回原方程后残差为 0（恒成立）。",
                }
            else:
                return {
                    "status": "fail",
                    "equation": equation,
                    "solution": solution,
                    "residual": str(residual),
                    "explanation": f"✗ 回代验证失败：解代回原方程后残差为 {residual}，不等于 0。解可能有误。",
                }

        except Exception as e:
            return {
                "status": "error",
                "equation": equation,
                "solution": solution,
                "error": f"sympy 计算失败: {e}",
                "explanation": "回代计算过程出错，可能是方程或解的表达形式不被 sympy 支持。",
            }

    except ValueError as e:
        return {
            "status": "error",
            "equation": equation,
            "solution": solution,
            "error": str(e),
            "explanation": f"输入表达式包含禁止的标识符或语法错误: {e}",
        }
    except Exception as e:
        return {
            "status": "error",
            "equation": equation,
            "solution": solution,
            "error": str(e),
            "explanation": f"回代验证过程出错: {e}",
        }


# ═══════════════════════════════════════════════════════════════════════════
# 4. 知识库交叉验证 — verify_knowledge
# ═══════════════════════════════════════════════════════════════════════════

def verify_knowledge(quantity: str, value: float, unit: str = "",
                     tolerance: float = 0.1) -> dict:
    """知识库交叉验证：在科学知识库中搜索已知值并比较。

    Args:
        quantity: 物理量名称（如 "hydrogen ground state energy"）
        value: 计算得到的数值
        unit: 单位（如 "eV"）
        tolerance: 允许的相对偏差（默认 0.1 = 10%）

    Returns:
        {"status": "pass"/"fail"/"unknown", "kb_results": [...], "closest_match": {...}, "explanation": str}
    """
    try:
        from ..memory.science_kb_ingest import search_knowledge
        kb_result = search_knowledge(quantity, collection="all", top_k=5)
    except (ImportError, ModuleNotFoundError):
        # 直接 import 时回退到 scipy.constants
        const_check = _check_known_constant(quantity, value, unit, tolerance)
        if const_check:
            return const_check
        return {
            "status": "unknown",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "explanation": "知识库不可用。建议启动完整服务后再验证。",
        }
    except Exception as e:
        return {
            "status": "error",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "error": f"知识库查询失败: {e}",
        }

    if not kb_result.get("results"):
        # 没有知识库结果时，用 scipy.constants 作为后备
        const_check = _check_known_constant(quantity, value, unit, tolerance)
        if const_check:
            return const_check
        return {
            "status": "unknown",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "explanation": "⚠ 知识库中未找到相关参考值，无法交叉验证。建议查阅教材或权威数据源确认。",
        }

    # 提取知识库结果中的数值信息
    # 用正则尝试从 content 中提取数值
    kb_values = []
    for r in kb_result["results"]:
        content = r.get("content", "")
        citation = r.get("citation", "")
        nums = re.findall(r'([\d.]+(?:e[+-]?\d+)?)\s*([A-Za-zμΩÅ°%]+(?:/[A-Za-z·]+)?)', content)
        for n_str, u in nums:
            try:
                kb_values.append({
                    "value": float(n_str),
                    "unit": u,
                    "citation": citation,
                    "confidence_tier": r.get("confidence_tier", "other"),
                    "snippet": content[:200],
                })
            except ValueError:
                pass

    if not kb_values:
        return {
            "status": "unknown",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "kb_count": len(kb_result["results"]),
            "explanation": "知识库找到相关文档，但未能从中提取数值。请人工交叉检查。",
        }

    # 找最接近的值
    best_match = None
    best_deviation = float("inf")
    for kv in kb_values:
        if unit and kv["unit"] != unit:
            continue  # 单位不同，跳过
        deviation = abs(kv["value"] - value) / max(abs(kv["value"]), 1e-30)
        if deviation < best_deviation:
            best_deviation = deviation
            best_match = {**kv, "deviation": deviation}

    if best_match is None:
        return {
            "status": "unknown",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "kb_matches": len(kb_values),
            "explanation": f"知识库找到 {len(kb_values)} 个数值但单位不匹配，无法直接比较。",
        }

    if best_deviation <= tolerance:
        return {
            "status": "pass",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "reference_value": best_match["value"],
            "reference_unit": best_match["unit"],
            "reference_source": best_match["citation"],
            "deviation": round(best_deviation, 4),
            "explanation": f"✓ 与知识库参考值 {best_match['value']} {best_match['unit']} 偏差 {best_deviation:.2%}，在容差 {tolerance:.0%} 内。来源：{best_match['citation']}",
        }
    else:
        return {
            "status": "fail",
            "quantity": quantity,
            "value": value,
            "unit": unit,
            "reference_value": best_match["value"],
            "reference_unit": best_match["unit"],
            "reference_source": best_match["citation"],
            "deviation": round(best_deviation, 4),
            "explanation": f"✗ 与知识库参考值 {best_match['value']} {best_match['unit']} 偏差 {best_deviation:.2%}，超过容差 {tolerance:.0%}。请重新检查计算。",
        }


def _check_known_constant(quantity: str, value: float, unit: str,
                          tolerance: float) -> dict | None:
    """使用 scipy.constants 检查已知物理常数。"""
    key = quantity.lower().replace(" ", "_").replace("-", "_")

    # 映射中文/英文名到 scipy.constants
    const_map = {
        "speed_of_light": ("c", _const.c),
        "light_speed": ("c", _const.c),
        "elementary_charge": ("e", _const.e),
        "electron_charge": ("e", _const.e),
        "planck_constant": ("h", _const.h),
        "reduced_planck_constant": ("hbar", _const.hbar),
        "boltzmann_constant": ("k", _const.k),
        "avogadro_number": ("N_A", _const.N_A),
        "avogadro_constant": ("N_A", _const.N_A),
        "gas_constant": ("R", _const.R),
        "gravitational_constant": ("G", _const.G),
        "electron_mass": ("m_e", _const.m_e),
        "proton_mass": ("m_p", _const.m_p),
        "neutron_mass": ("m_n", _const.m_n),
        "bohr_radius": ("a_0", _const.physical_constants["Bohr radius"][0]),
        "rydberg_constant": ("R_inf", _const.Rydberg),
        "stefan_boltzmann_constant": ("sigma", _const.sigma),
        "wien_displacement": ("b", _const.Wien),
        "magnetic_constant": ("mu_0", _const.mu_0),
        "electric_constant": ("epsilon_0", _const.epsilon_0),
        "atomic_mass_constant": ("u", _const.atomic_mass),
        "fine_structure_constant": ("alpha", _const.alpha),
        "hydrogen_ground_state_energy": ("E_h", -13.605693122994 * _const.eV / _const.e),
    }

    if key in const_map:
        name, const_val = const_map[key]
        deviation = abs(value - const_val) / max(abs(const_val), 1e-30)
        if deviation <= tolerance:
            return {
                "status": "pass",
                "quantity": quantity,
                "value": value,
                "unit": unit,
                "reference_value": const_val,
                "reference_source": f"CODATA: {name}",
                "deviation": round(deviation, 4),
                "explanation": f"✓ 与 CODATA 常数 {name}={const_val} 偏差 {deviation:.2%}。",
            }
        else:
            return {
                "status": "fail",
                "quantity": quantity,
                "value": value,
                "unit": unit,
                "reference_value": const_val,
                "reference_source": f"CODATA: {name}",
                "deviation": round(deviation, 4),
                "explanation": f"✗ 与 CODATA 常数 {name}={const_val} 偏差 {deviation:.2%}，超过容差。",
            }

    return None
