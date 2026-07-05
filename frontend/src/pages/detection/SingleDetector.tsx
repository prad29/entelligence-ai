import { useState } from 'react'
import { useDetect } from '@/hooks/useDetect'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Textarea } from '@/components/ui/Textarea'
import { ResultCard } from './ResultCard'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Sparkles, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'

const EXAMPLE_CHIPS = [
  'VIP | UltraAVX Cineplex | Dolby Atmos',
  '4DX | IMAX | BTX',
  'CINÉ XL®',
  'XD | Stadium Seating',
  'Heated Recliners, Stadium Seating',
  'ScreenX | Dolby Atmos',
  'MX4D | 4K Laser',
]

function SingleDetector() {
  const [amenityString, setAmenityString] = useState('')
  const [circuitName, setCircuitName] = useState('')
  const { result, loading, error, detect, reset } = useDetect()

  const handleDetect = async () => {
    if (!amenityString.trim()) return
    await detect({ amenity: amenityString, circuit_name: circuitName || undefined })
  }

  const handleChip = (chip: string) => {
    setAmenityString(chip)
    reset()
  }

  const handleReset = () => {
    setAmenityString('')
    setCircuitName('')
    reset()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <Sparkles className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Single Detection</CardTitle>
              <CardDescription>Detect the screen format from an amenity string</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {/* Example chips */}
          <div>
            <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-2">Try an example:</p>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_CHIPS.map((chip) => (
                <button
                  key={chip}
                  onClick={() => handleChip(chip)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs font-medium transition-all duration-150',
                    amenityString === chip
                      ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                      : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800'
                  )}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>

          {/* Inputs */}
          <Textarea
            label="Amenity String"
            id="amenity-string"
            value={amenityString}
            onChange={(e) => {
              setAmenityString(e.target.value)
              if (result) reset()
            }}
            placeholder="e.g. IMAX | Dolby Atmos | VIP Seating"
            rows={3}
          />

          <Input
            label="Circuit Name (optional)"
            id="circuit-name"
            value={circuitName}
            onChange={(e) => setCircuitName(e.target.value)}
            placeholder="e.g. AMC, Cineplex"
          />

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              onClick={() => void handleDetect()}
              loading={loading}
              disabled={!amenityString.trim()}
              className="flex-1"
            >
              <Sparkles className="h-4 w-4" />
              Detect Format
            </Button>
            {(amenityString || result) && (
              <Button variant="ghost" size="icon" onClick={handleReset} aria-label="Reset">
                <RotateCcw className="h-4 w-4" />
              </Button>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}
        </CardContent>
      </Card>

      {result && <ResultCard result={result} />}
    </div>
  )
}

export { SingleDetector }
