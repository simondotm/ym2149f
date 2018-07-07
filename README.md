# ym2149f
By [@simondotm](https://github.com/simondotm/ym2149f) 

Experiments with conversion of Atari ST [YM2149F](https://en.wikipedia.org/wiki/General_Instrument_AY-3-8910) chip tunes to [SN76489](https://en.wikipedia.org/wiki/Texas_Instruments_SN76489)

## Intro
Back in the 80's many of my friends had 16-bit machines before I did and the Atari ST was one I really wanted. Although the Amiga had more power & better audio, for some reason the Atari chip tunes always seemed really cool to me. Well, I never did get an ST, but I spent many a happy hour with my pals enjoying ST demo scene stuff.

Recently I got back into messing with chip tunes on the venerable BBC Micro just for fun with [@bitshifters](https://bitshifters.github.io/), when it occurred to me that maybe I could port some of my favourite tunes from the ST to the BBC Micro SN76489 sound chip.

Turns out that wasn't such a crazy idea because the Atari ST uses a PSG that's pretty similar to the Beeb.

This repo contains some scripts I've hacked together to do the conversion.

## How it works

Atari ST music is archived these days in two formats:

* .SNDH format
* .YM file format

SNDH files are no use to us, because they are bundles of tune data along with the actual 68000 assembler code (!) for music playback. Hardcore.

[YM files](http://leonard.oxg.free.fr/ymformat.html) on the other hand are great. They are a nice and easy stream of sound chip register values over time, much like VGM files. It's suprising that VGM doesn't already support this type of PSG but there we go.

### Chip Specs
Anyway, there's quite a lot of similarity between these two PSG's:

| Feature        | YM2149           | SN76489  |
| ------------- |:-------------:|:-----:|
| Tone Generator Type      | Square Wave | Square Wave |
| Number of Tone Generators | 3 | 3 |
| Dynamic Range of Tone Generators | 12 bits | 10 bits |
| Hardware Clock divider | 8 | 16 |
| Noise Generator Type | LFSR | LFSR (White or Periodic Noise ) |
| Number of Noise Generators | 1 | 1 |
| Dynamic Range of Noise Generators | 5 bits | 10 bits |
| Number of Noise Mixers | 3 | 1 |
| Envelope Generators | 1 | None |
| Dynamic Range of Output Levels | 4 bits | 4 bits |

### Square waves on PSG's
The duty cycle of the squarewaves output by these kinds of PSG's is a function of the clock that drives the sound chip, which on the BBC Micro is 4Mhz (on Sega Master System it might be 3.579545 MHz for NTSC or 4.43361875 Mhz for PAL ). 

For PSGs like the SN76489 and YM2149, setting a tone pitch register value is essentially setting an internal counter that inverts the output level each time it reaches 0, and therefore controls the square wave output / frequency.

This is important to understand because to create specific output audio frequencies, you have to know what the target chip clock speed will be, in order to know what register values to set. Further, the clock speed of the chip dictates the range of frequencies it is capable of generating.

### Frequency Ranges
Frequency (Hz) is calculated for tone registers as follows:

* YM tone register setting of N is: `CLOCK / (2.0 * 8.0 * N)`
* SN tone register setting of N is: `CLOCK / (2.0 * 16.0 * N)`
* SN periodic noise register setting of N is: `CLOCK / (2.0 * 16.0 * 15.0 * N)`

The 2.0 bit is the square wave duty cycle, and the 8.0 or 16.0 bit is the hardware clock divider.

So given that:
* The Atari ST YM PSG is typically clocked at 2Mhz.
* The BBC Micro SN PSG is clocked at 4Mhz.

We can calculate the frequency range of these systems as:
```
YM Tone Frequency range from 30.525030525Hz to 125000.0Hz
SN Tone Frequency range from 122.189638319Hz to 125000.0Hz
SN Bass/PN Frequency range from 8.14597588791Hz to 8333.33333333Hz
```
As we can see, the YM chip has slightly better dynamic range that the SN chip in the low end, which is why using the periodic noise generator on the SN chip allows us to 'simulate' that missing frequency range as best we can.

Periodic noise on the BBC Micro however is a basket case in it's own right. See [here for more info](https://gist.github.com/simondotm/84a08066469866d7f2e6aad875e598be) on that if you are interested.

### Conversion Process

What this all means is that it is reasonably easy to 'map' data for YM registers to data for SN registers, and recreate 'more-or-less' the same output.

The basic conversion process is as follows:
1. Scan through the YM file, one 50Hz frame at a time
1. Convert as best as possible the register updates going to the YM chip in each frame to an equivalent 'best fit' register set update for the SN chip
1. Map the 4-bit output levels directly for each channel from YM -> SN
1. _Ignore hardware envelopes (for now)_
1. If tone frequencies are too low for the SN chip, map them to 'periodic noise' on the SN chip (typically bass lines), but give drum/noise sounds priority 
1. If tone frequencies are still too high or too low for the SN chip, bring them back into range an octave at a time
1. Where noise mixer is enabled, emulate this mix by computing aggregate volume for the SN noise channel across the 3 YM mixers -> 1 SN noise output
1. _Ignore digi-drums (for now)_
1. Spit out a VGM file of the converted YM tune

Now we can play the conversion VGM in our favourite VGM player, or for playback on an actual SN chip, simply throw the packet of SN register writes (upto 11) at the chip each 50Hz frame and away we go.

### Example Conversion
See Bitshifters' [Twisted Brain Demo](https://bitshifters.github.io/posts/prods/bs-twisted-brain.html) for an example of a conversion of [Mad Max's epic 'There aren't any sheep in outer mongolia'](https://www.youtube.com/watch?v=2mwbBwAjt8c) tune. It's 3m33s of pretty busy sound chip updates compressed down to 22Kb and unpacked/streamed to the PSG at 50Hz, so not the most efficient data format, but it sounds surprisingly good for an SN chip.


## Challenges
### Envelopes
Quite a lot of Atari ST tunes don't really use the envelope feature much, which makes conversions of these tunes a bit easier. 

However, the tunes that do use them, have a much different tonal landscape and sound pretty bad (harsh) when ported to the SN. 

That said, envelopes can _I feel_ be emulated on an SN chip, as they are simple modulators of the volume for each tone generator, but supporting them will require much more CPU time in a hardware player, since some of the envelopes in some tunes require updates at reasonably high frequencies - ie. upto tens of kilohertz. 

### Noise mixer
Some tunes use quite subtle combinations of the 3 noise mixers, which are very tricky if not impossible to 'fully' emulate on an SN chip, but when converted using a mixed approach, they don't sound too far off. Also worth noting that the YM and SN chips use different pseudo-white-noise generators so that changes tonal effect too.


### Digi drums
Digi drums were a later innovation in the Atari ST music journey. Essentially they are just envelopes that are driven by 4-bit waveform samples. The sample data is used to modulate the volume level for a tone generator in software rather than hardware. Again, these may be technically feasible for the SN chip, but like envelopes above, will require a lot of CPU time.

## Conclusions
So that pretty much sums it up. I was actually quite surprised how well the results turned out, so when I get chance I'll spend a bit more time on this as I think there are plenty of improvement areas!



## References

See [SMS Power](http://www.smspower.org/Development/SN76489) for some great technical info about the SN76489 PSG.

Chip manufacturer data sheets are also very useful references:
* [SN76489](https://github.com/simondotm/ym2149f/blob/master/doc/SN76489.pdf)
* [YM2149](https://github.com/simondotm/ym2149f/blob/master/doc/ym2149_DS.pdf)

Other resources relating to [Atari ST music](Resources.md).














