// Material Symbols Outlined — matches the Stitch design's iconography.
// Usage: <Icon name="check_circle" size={16} fill />

interface Props {
  name: string;
  size?: number;
  fill?: boolean;
  className?: string;
}

export function Icon({ name, size = 20, fill = false, className }: Props) {
  return (
    <span
      className={`ms${className ? " " + className : ""}`}
      style={{
        fontSize: size,
        fontVariationSettings: `'FILL' ${fill ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 20`,
      }}
      aria-hidden
    >
      {name}
    </span>
  );
}
