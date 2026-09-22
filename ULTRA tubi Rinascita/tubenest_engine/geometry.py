"""Decoded line, circular-arc and rational B-spline geometry."""
from dataclasses import dataclass
from bisect import bisect_right
import math
import struct
from .bcmp import Block, Record, FormatError, vector, read_vector


@dataclass
class Line:
    start: tuple
    direction: tuple

    def at(self, t):
        return tuple(a + t*b for a, b in zip(self.start, self.direction))

    def sample(self, n=32):
        return [self.at(0), self.at(1)]


@dataclass
class Polyline:
    points: list

    def at(self, t):
        if not self.points:
            raise FormatError("Empty polyline")
        if len(self.points) == 1:
            return self.points[0]
        t = min(1.0, max(0.0, float(t)))
        scaled = t * (len(self.points) - 1)
        i = min(len(self.points) - 2, int(scaled))
        local = scaled - i
        a, b = self.points[i], self.points[i + 1]
        return tuple(x + local * (y - x) for x, y in zip(a, b))

    def sample(self, n=32):
        return list(self.points)


@dataclass
class Arc2D:
    center: tuple
    radius: float
    angle: float
    sweep: float
    flag: int = 0

    def at(self, t):
        a = self.angle + t*self.sweep
        return (self.center[0] + self.radius*math.cos(a), self.center[1] + self.radius*math.sin(a))

    def sample(self, n=32):
        return [self.at(i/n) for i in range(n+1)]


@dataclass
class Spline:
    degree: int
    knots: list
    points: list
    weights: list
    flag: int = 0

    def __post_init__(self):
        if not 1 <= self.degree < len(self.points):
            raise FormatError("Invalid spline degree/control point count")
        if len(self.knots) != len(self.points) + self.degree + 1 or len(self.weights) != len(self.points):
            raise FormatError("Inconsistent spline counts")
        values = self.knots + self.weights + [v for p in self.points for v in p]
        if not all(math.isfinite(v) for v in values) or any(w <= 0 for w in self.weights):
            raise FormatError("Nonfinite spline data or nonpositive weight")
        if any(a > b for a,b in zip(self.knots,self.knots[1:])) or self.knots[self.degree] >= self.knots[len(self.points)]:
            raise FormatError("Invalid spline knot sequence")

    def at(self, t):
        """Evaluate normalized t in [0,1] using homogeneous de Boor."""
        p = self.degree
        u = self.knots[p] + min(1., max(0., t))*(self.knots[len(self.points)] - self.knots[p])
        k = min(len(self.points)-1, max(p, bisect_right(self.knots, u)-1))
        d = [[*(v*self.weights[j] for v in self.points[j]), self.weights[j]] for j in range(k-p, k+1)]
        for r in range(1, p+1):
            for j in range(p, r-1, -1):
                i = k-p+j
                denom = self.knots[i+p-r+1] - self.knots[i]
                a = (u-self.knots[i])/denom if denom else 0.
                d[j] = [(1-a)*v + a*w for v,w in zip(d[j-1], d[j])]
        return tuple(v/d[p][-1] for v in d[p][:-1])

    def sample(self, n=32):
        lo, hi = self.knots[self.degree], self.knots[len(self.points)]
        ts = {i/n for i in range(n+1)} | {(u-lo)/(hi-lo) for u in self.knots if lo<=u<=hi}
        return [self.at(t) for t in sorted(ts)]

    def payload(self):
        out = struct.pack("<4I", len(self.points), self.degree, len(self.knots), self.flag)
        out += struct.pack("<" + "d"*len(self.knots), *self.knots)
        for p,w in zip(self.points, self.weights):
            out += vector(p) + struct.pack("<d", w)
        return out

    @classmethod
    def decode(cls, data):
        if len(data)<16:
            raise FormatError("Truncated spline")
        n,p,k,flag = struct.unpack_from("<4I", data)
        if len(data) != 16 + 8*k + 36*n:
            raise FormatError("Spline payload length mismatch")
        knots = list(struct.unpack_from("<"+"d"*k, data, 16))
        points,weights = [],[]
        off=16+8*k
        for _ in range(n):
            points.append(read_vector(data,off))
            weights.append(struct.unpack_from("<d",data,off+28)[0])
            off+=36
        return cls(p,knots,points,weights,flag)


def primitives(record):
    result=[]
    for b in record.blocks:
        if b.name in ("Line2D","Line3D") and b.payload:
            dim=2 if b.name=="Line2D" else 3
            if len(b.payload)!=2*(4+8*dim):
                raise FormatError("Unexpected line payload size")
            result.append(Line(read_vector(b.payload,0,dim),read_vector(b.payload,4+8*dim,dim)))
        elif b.name=="CircularArc2D" and b.payload:
            if len(b.payload)!=48:
                raise FormatError("Unexpected arc payload size")
            result.append(Arc2D(read_vector(b.payload,0,2),*struct.unpack_from("<dddI",b.payload,20)))
        elif b.name=="Spline3D" and b.payload:
            result.append(Spline.decode(b.payload))
        elif b.name=="Polyline3D" and b.payload:
            if len(b.payload) < 4:
                raise FormatError("Truncated polyline")
            count = struct.unpack_from("<I", b.payload)[0]
            if len(b.payload) != 4 + count * 28:
                raise FormatError("Polyline payload length mismatch")
            points = [read_vector(b.payload, 4 + i * 28, 3) for i in range(count)]
            result.append(Polyline(points))
    return result


def curve_blocks(curve):
    if isinstance(curve, Line):
        name="Line"+str(len(curve.start))+"D"
        payload=vector(curve.start)+vector(curve.direction)
    elif isinstance(curve,Arc2D):
        name="CircularArc2D"
        payload=vector(curve.center)+struct.pack("<dddI",curve.radius,curve.angle,curve.sweep,curve.flag)
    else:
        name="Spline3D"
        payload=curve.payload()
    blocks=[Block(name),Block(name)]
    if name.endswith("3D"):
        blocks.append(Block("Curve3D",payload=bytes(4)))
    blocks.append(Block(name,payload=payload))
    return blocks


def reverse_curve(curve):
    if isinstance(curve,Line):
        return Line(curve.at(1),tuple(-v for v in curve.direction))
    if isinstance(curve,Arc2D):
        return Arc2D(curve.center,curve.radius,curve.angle+curve.sweep,-curve.sweep,curve.flag)
    total=curve.knots[0]+curve.knots[-1]
    return Spline(curve.degree,[total-k for k in reversed(curve.knots)],list(reversed(curve.points)),list(reversed(curve.weights)),curve.flag)


def composite(curves, dimension=3):
    name=f"CompositeCurve{dimension}D"
    blocks=[Block(name)]
    if dimension==3:
        blocks.append(Block("Curve3D",payload=bytes(4)))
    blocks.append(Block(name,payload=struct.pack("<I",len(curves))))
    for curve in curves:
        blocks.extend(curve_blocks(curve))
    return Record("TGe"+name,blocks)


def bounds(points):
    points=list(points)
    if not points:
        raise FormatError("No geometry points")
    return [list(map(min,zip(*points))), list(map(max,zip(*points)))]


def rounded_square(side, radius):
    h=side/2; c=h-radius
    result=[]
    for a in (math.pi/2,math.pi,3*math.pi/2,2*math.pi):
        ca,sa=round(math.cos(a)),round(math.sin(a))
        # Rotate the upper side and its following top-left corner.
        def rot(x,y): return (sa*x+ca*y,-ca*x+sa*y)
        result.append(Line(rot(c,h),rot(-2*c,0)))
        result.append(Arc2D(rot(-c,c),radius,a,math.pi/2))
    return result


def rounded_rectangle(width,height,radius):
    hx,hy=width/2,height/2
    cx,cy=hx-radius,hy-radius
    return [Line((cx,hy),(-2*cx,0.)),Arc2D((-cx,cy),radius,math.pi/2,math.pi/2),
            Line((-hx,cy),(0.,-2*cy)),Arc2D((-cx,-cy),radius,math.pi,math.pi/2),
            Line((-cx,-hy),(2*cx,0.)),Arc2D((cx,-cy),radius,3*math.pi/2,math.pi/2),
            Line((hx,-cy),(0.,2*cy)),Arc2D((cx,cy),radius,0.,math.pi/2)]


def circle_quarters(radius):
    return [Arc2D((0.,0.),radius,a,math.pi/2) for a in (0.,math.pi/2,math.pi,3*math.pi/2)]


def lift_section(side,radius,plane):
    """Nine pieces, matching the manual sample's seam at middle of upper side.

    Circular corners are exact rational cubics, each made from two 45-degree
    rational quadratic Beziers elevated to degree three.
    """
    def lift(p):
        return (p[0],p[1],plane[0]+plane[1]*p[0]+plane[2]*p[1])
    return lift_curves(rounded_square(side,radius),lift,split_first=True)


def lift_curves(curves,lift,split_first=False):
    """Map 2D lines/arcs into 3D via an affine mapping, retaining exact circles."""
    ordered=list(curves)
    if split_first:
        first=curves[0];mid=first.at(.5)
        if not isinstance(first,Line):raise ValueError('Only a line seam can be split')
        ordered=[Line(mid,tuple(b-a for a,b in zip(mid,first.at(1))))]+curves[1:]+[Line(first.start,tuple(b-a for a,b in zip(first.start,mid)))]
    result=[]
    for curve in ordered:
        if isinstance(curve,Line):
            p,q=lift(curve.at(0)),lift(curve.at(1))
            result.append(Line(p,tuple(b-a for a,b in zip(p,q))))
        else:
            points=[];weights=[]
            for half in range(2):
                a=curve.angle+half*curve.sweep/2; b=a+curve.sweep/2; m=(a+b)/2; w=math.cos((b-a)/2)
                xy=[(curve.center[0]+curve.radius*math.cos(a),curve.center[1]+curve.radius*math.sin(a)),
                    (curve.center[0]+curve.radius*math.cos(m)/w,curve.center[1]+curve.radius*math.sin(m)/w),
                    (curve.center[0]+curve.radius*math.cos(b),curve.center[1]+curve.radius*math.sin(b))]
                h=[[*[v*ww for v in p],ww] for p,ww in zip(xy,[1.,w,1.])]
                elevated=[h[0],[(x+2*y)/3 for x,y in zip(h[0],h[1])],[(2*x+y)/3 for x,y in zip(h[1],h[2])],h[2]]
                for q in elevated[0 if half==0 else 1:]:
                    points.append(lift((q[0]/q[2],q[1]/q[2])));weights.append(q[2])
            result.append(Spline(3,[0.]*4+[.5]*3+[1.]*4,points,weights))
    return result
