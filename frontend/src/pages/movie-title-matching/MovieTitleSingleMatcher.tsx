import { useState } from 'react'
import { useMovieTitleMatch } from '@/hooks/useMovieTitleMatch'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Input } from '@/components/ui/Input'
import { MovieTitleMatchCard } from './MovieTitleMatchCard'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Clapperboard, Eye, Globe2, RotateCcw, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Market } from './MovieTitleMatchingPage'

const EXAMPLE_CHIPS = [
  'Moana (Live Action)',
  'MegaReelDeal Deathly Hallows: Part 1',
  'Love Island Season Finale',
  'KIDSHOW SMURFS',
  'Harry Potter 3',
  'Fight Club',
  'TOY STORY',
  '$5 Batman',
  'Night Nurse (Open Captioning)',
  'HP (OV 1-4) Germany',
]

interface MovieTitleSingleMatcherProps {
  market: Market
  onMarketChange: (m: Market) => void
}

function MovieTitleSingleMatcher({ market, onMarketChange }: MovieTitleSingleMatcherProps) {
  const [title, setTitle] = useState('')
  const [theater, setTheater] = useState('')
  const [showDate, setShowDate] = useState('')
  const [ticketingUrl, setTicketingUrl] = useState('')
  const [country, setCountry] = useState('')
  const [posterVision, setPosterVision] = useState(false)
  const { result, loading, error, match, reset } = useMovieTitleMatch()

  const isIntl = market === 'international'

  const handleMatch = async () => {
    if (!title.trim()) return
    if (isIntl && !country.trim()) return
    await match({
      title: title.trim(),
      ...(theater.trim() ? { theater: theater.trim() } : {}),
      ...(showDate ? { show_date: showDate } : {}),
      ...(ticketingUrl.trim() ? { ticketing_url: ticketingUrl.trim() } : {}),
      ...(posterVision ? { use_poster_vision: true } : {}),
      ...(isIntl ? { market: 'international', country: country.trim() } : {}),
    })
  }

  const handleChip = (chip: string) => {
    setTitle(chip)
    reset()
  }

  const handleReset = () => {
    setTitle('')
    setTheater('')
    setShowDate('')
    setTicketingUrl('')
    setCountry('')
    setPosterVision(false)
    reset()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <Clapperboard className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Single Match</CardTitle>
              <CardDescription>Match a movie title against the Movie Master database</CardDescription>
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
                  type="button"
                  onClick={() => handleChip(chip)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs font-medium transition-all duration-150',
                    title === chip
                      ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                      : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800'
                  )}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>

          {/* Title input */}
          <Textarea
            label="Movie Title"
            id="movie-title"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value)
              if (result) reset()
            }}
            placeholder="e.g. Moana (Live Action)"
            rows={3}
          />

          {/* Optional fields */}
          <Input
            label="Theater (optional)"
            id="theater"
            value={theater}
            onChange={(e) => setTheater(e.target.value)}
            placeholder="e.g. Falmouth Luxury Cinemas"
          />
          <Input
            label="Show Date (optional)"
            id="show-date"
            type="text"
            inputMode="numeric"
            placeholder="YYYY-MM-DD"
            value={showDate}
            onChange={(e) => setShowDate(e.target.value.trim())}
            onPaste={(e) => {
              const pasted = e.clipboardData.getData('text')
              if (!pasted.includes('\n')) return
              e.preventDefault()
              const firstDate = pasted
                .split('\n')
                .map((line) => line.trim())
                .find((line) => /^\d{4}-\d{2}-\d{2}$/.test(line))
              setShowDate(firstDate ?? pasted.split('\n')[0].trim())
            }}
          />
          <Input
            label="Ticketing URL (optional)"
            id="ticketing-url"
            value={ticketingUrl}
            onChange={(e) => setTicketingUrl(e.target.value)}
            placeholder="https://..."
          />
          {isIntl && (
            <Input
              label="Country"
              id="country"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              placeholder="e.g. France"
            />
          )}

          {/* Poster vision + Domestic/International toggles, side by side */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setPosterVision(!posterVision)}
              className={cn(
                'flex flex-1 items-center gap-2.5 rounded-xl border px-3.5 py-3 text-sm transition-all duration-200 text-left min-w-0',
                posterVision
                  ? 'border-violet-400 dark:border-violet-600 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                  : 'border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/40'
              )}
            >
              {/* Track */}
              <div className={cn(
                'relative h-5 w-9 rounded-full transition-colors duration-200 shrink-0',
                posterVision ? 'bg-violet-500' : 'bg-zinc-300 dark:bg-zinc-600'
              )}>
                {/* Thumb */}
                <span className={cn(
                  'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
                  posterVision ? 'translate-x-4' : 'translate-x-0'
                )} />
              </div>
              <Eye className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium truncate">Poster Vision</span>
            </button>

            <button
              type="button"
              onClick={() => onMarketChange(isIntl ? 'domestic' : 'international')}
              className={cn(
                'flex flex-1 items-center gap-2.5 rounded-xl border px-3.5 py-3 text-sm transition-all duration-200 text-left min-w-0',
                isIntl
                  ? 'border-[#4A9FD4] dark:border-[#4A9FD4]/70 bg-[#4A9FD4]/5 dark:bg-[#4A9FD4]/10 text-[#4A9FD4]'
                  : 'border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/40'
              )}
            >
              {/* Track */}
              <div className={cn(
                'relative h-5 w-9 rounded-full transition-colors duration-200 shrink-0',
                isIntl ? 'bg-[#4A9FD4]' : 'bg-zinc-300 dark:bg-zinc-600'
              )}>
                {/* Thumb */}
                <span className={cn(
                  'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
                  isIntl ? 'translate-x-4' : 'translate-x-0'
                )} />
              </div>
              <Globe2 className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium truncate">International</span>
            </button>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              onClick={() => void handleMatch()}
              loading={loading}
              disabled={!title.trim() || (isIntl && !country.trim())}
              className="flex-1"
            >
              <Search className="h-4 w-4" />
              Find Match
            </Button>
            {(title || theater || showDate || ticketingUrl || country || result) && (
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

      {result && <MovieTitleMatchCard result={result} />}
    </div>
  )
}

export { MovieTitleSingleMatcher }
