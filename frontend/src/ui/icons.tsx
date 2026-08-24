import Svg, { Circle, Line, Path, Polygon, Rect } from 'react-native-svg';

// The live app's inline stroke icons, copied path-for-path from index.html (24x24 viewBox,
// stroke=currentColor, stroke-width 2, round caps/joins — Lucide-style but hand-authored).
// Ionicons approximations read noticeably different (the calendar especially), so these
// are verbatim.

interface IconProps {
  size?: number;
  color: string;
  strokeWidth?: number;
}

function S({ size = 18, children }: { size?: number; children: React.ReactNode }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      {children}
    </Svg>
  );
}

const stroke = (color: string, strokeWidth = 2) =>
  ({ stroke: color, strokeWidth, strokeLinecap: 'round', strokeLinejoin: 'round' }) as const;

export function HomeIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Path d="M3 11.5 12 4l9 7.5" {...stroke(color, strokeWidth)} />
      <Path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9" {...stroke(color, strokeWidth)} />
    </S>
  );
}

export function PersonIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Circle cx={12} cy={8} r={4} {...stroke(color, strokeWidth)} />
      <Path d="M4 20c0-4 4-6 8-6s8 2 8 6" {...stroke(color, strokeWidth)} />
    </S>
  );
}

export function SearchIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Circle cx={11} cy={11} r={7} {...stroke(color, strokeWidth)} />
      <Line x1={21} y1={21} x2={16.65} y2={16.65} {...stroke(color, strokeWidth)} />
    </S>
  );
}

export function CalendarIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Rect x={3} y={5} width={18} height={16} rx={2} {...stroke(color, strokeWidth)} />
      <Line x1={16} y1={3} x2={16} y2={7} {...stroke(color, strokeWidth)} />
      <Line x1={8} y1={3} x2={8} y2={7} {...stroke(color, strokeWidth)} />
      <Line x1={3} y1={10} x2={21} y2={10} {...stroke(color, strokeWidth)} />
    </S>
  );
}

export function ListIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Line x1={8} y1={6} x2={21} y2={6} {...stroke(color, strokeWidth)} />
      <Line x1={8} y1={12} x2={21} y2={12} {...stroke(color, strokeWidth)} />
      <Line x1={8} y1={18} x2={21} y2={18} {...stroke(color, strokeWidth)} />
      <Line x1={3} y1={6} x2={3.01} y2={6} {...stroke(color, strokeWidth)} />
      <Line x1={3} y1={12} x2={3.01} y2={12} {...stroke(color, strokeWidth)} />
      <Line x1={3} y1={18} x2={3.01} y2={18} {...stroke(color, strokeWidth)} />
    </S>
  );
}

export function RefreshIcon({ size, color, strokeWidth = 2.5 }: IconProps) {
  return (
    <S size={size}>
      <Path d="M21 12a9 9 0 1 1-2.6-6.3" {...stroke(color, strokeWidth)} />
      <Path d="M21 4v5h-5" {...stroke(color, strokeWidth)} />
    </S>
  );
}

// Calendar with a check mark — the Google Calendar sync button.
export function CalendarSyncIcon({ size, color, strokeWidth }: IconProps) {
  return (
    <S size={size}>
      <Rect x={3} y={5} width={18} height={16} rx={2} {...stroke(color, strokeWidth)} />
      <Line x1={16} y1={3} x2={16} y2={7} {...stroke(color, strokeWidth)} />
      <Line x1={8} y1={3} x2={8} y2={7} {...stroke(color, strokeWidth)} />
      <Line x1={3} y1={10} x2={21} y2={10} {...stroke(color, strokeWidth)} />
      <Path d="M9 15.5l1.8 1.8L15 13.5" {...stroke(color, strokeWidth)} />
    </S>
  );
}

const STAR_POINTS = '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2';

export function StarIcon({ size, color, filled }: IconProps & { filled?: boolean }) {
  return (
    <S size={size}>
      <Polygon
        points={STAR_POINTS}
        fill={filled ? color : 'none'}
        stroke={color}
        strokeWidth={filled ? 1 : 2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </S>
  );
}

export function XIcon({ size, color, strokeWidth = 2.5 }: IconProps) {
  return (
    <S size={size}>
      <Line x1={18} y1={6} x2={6} y2={18} {...stroke(color, strokeWidth)} />
      <Line x1={6} y1={6} x2={18} y2={18} {...stroke(color, strokeWidth)} />
    </S>
  );
}
