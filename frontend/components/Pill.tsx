type PillColor = "blue" | "green" | "amber" | "gray";

interface PillProps {
  label: string;
  color?: PillColor;
}

const BG: Record<PillColor, string> = {
  blue: "#EEF3FF",
  green: "#ECFDF5",
  amber: "#FFFBEB",
  gray: "#F3F4F6",
};
const TEXT: Record<PillColor, string> = {
  blue: "#1D4ED8",
  green: "#065F46",
  amber: "#92400E",
  gray: "#374151",
};
const BORDER: Record<PillColor, string> = {
  blue: "#BFDBFE",
  green: "#A7F3D0",
  amber: "#FDE68A",
  gray: "#E5E7EB",
};

export default function Pill({ label, color = "blue" }: PillProps) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 8px",
        borderRadius: 6,
        fontSize: 11,
        fontWeight: 500,
        background: BG[color],
        color: TEXT[color],
        border: `1px solid ${BORDER[color]}`,
        letterSpacing: "0.01em",
      }}
    >
      {label}
    </span>
  );
}
