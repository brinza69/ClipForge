"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * Slider component — pure HTML range inputs styled via globals.css.
 * Replaces @base-ui/react Slider which injected a <script> tag
 * that Next.js rejects at render time.
 *
 * Supports single-value and dual-thumb (range) modes via the `value` prop.
 */
interface SliderProps {
  className?: string
  value?: number | readonly number[]
  defaultValue?: number | readonly number[]
  min?: number
  max?: number
  step?: number
  onValueChange?: (value: number | readonly number[]) => void
}

function Slider({
  className,
  value,
  defaultValue,
  min = 0,
  max = 100,
  step = 1,
  onValueChange,
}: SliderProps) {
  const values = React.useMemo(() => {
    if (Array.isArray(value)) return value as number[]
    if (typeof value === "number") return [value]
    if (Array.isArray(defaultValue)) return defaultValue as number[]
    if (typeof defaultValue === "number") return [defaultValue]
    return [min]
  }, [value, defaultValue, min])

  const isRange = values.length >= 2

  const handleChange = (index: number, newVal: number) => {
    if (!onValueChange) return
    if (isRange) {
      const next = [...values]
      next[index] = newVal
      if (index === 0 && next[0] > next[1]) next[0] = next[1]
      if (index === 1 && next[1] < next[0]) next[1] = next[0]
      onValueChange(next)
    } else {
      onValueChange(newVal)
    }
  }

  const leftPct = isRange ? ((values[0] - min) / (max - min)) * 100 : 0
  const rightPct = isRange
    ? ((values[1] - min) / (max - min)) * 100
    : ((values[0] - min) / (max - min)) * 100

  return (
    <div
      data-slot="slider"
      className={cn("relative flex w-full items-center select-none", className)}
      style={{ height: 20 }}
    >
      {/* Track background */}
      <div className="absolute left-0 right-0 h-1 rounded-full bg-muted" />
      {/* Filled range indicator */}
      <div
        data-slot="slider-range"
        className="absolute h-1 rounded-full bg-primary"
        style={{
          left: isRange ? `${leftPct}%` : "0%",
          right: `${100 - rightPct}%`,
        }}
      />
      {/* Thumb(s) — uses .slider-thumb-input from globals.css */}
      {isRange ? (
        <>
          <input
            type="range"
            min={min}
            max={max}
            step={step}
            value={values[0]}
            onChange={(e) => handleChange(0, Number(e.target.value))}
            className="slider-thumb-input absolute w-full"
            style={{ pointerEvents: "none", zIndex: 2 }}
          />
          <input
            type="range"
            min={min}
            max={max}
            step={step}
            value={values[1]}
            onChange={(e) => handleChange(1, Number(e.target.value))}
            className="slider-thumb-input absolute w-full"
            style={{ pointerEvents: "none", zIndex: 3 }}
          />
        </>
      ) : (
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={values[0]}
          onChange={(e) => handleChange(0, Number(e.target.value))}
          className="slider-thumb-input absolute w-full"
          style={{ zIndex: 2 }}
        />
      )}
    </div>
  )
}

export { Slider }
