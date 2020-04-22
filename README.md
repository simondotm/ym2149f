# ym2149f
By [@simondotm](https://github.com/simondotm/ym2149f) 

Experiments with conversion of Atari ST [YM2149F](https://en.wikipedia.org/wiki/General_Instrument_AY-3-8910) chip tunes to [SN76489](https://en.wikipedia.org/wiki/Texas_Instruments_SN76489)

## Intro
Back in the 80's many of my friends had 16-bit machines before I did and the Atari ST was one I really wanted. Although the Amiga had more power & better audio, for some reason the Atari chip tunes always seemed really cool to me. Well, I never did get an ST, but I spent many a happy hour with my pals enjoying ST demo scene stuff.

Recently I got back into messing with chip tunes on the venerable BBC Micro just for fun with [@bitshifters](https://bitshifters.github.io/), when it occurred to me that maybe I could port some of my favourite tunes from the ST to the BBC Micro SN76489 sound chip.

Turns out that wasn't such a crazy idea because the Atari ST uses a PSG that's pretty similar to the Beeb.

This repo contains a script I've created to do the conversion so that you can listen to these tunes on an SN76489 sound chip as well.

## Usage
**NEW!** The script is now compatible with both Python 2.7 and Python 3.x.

```
ym2sn.py : Convert Atari ST .YM files to SN76489 VGM music files
Written in 2019 by Simon Morris, https://github.com/simondotm/ym2149f

usage: ym2sn.py [-h] [-o <output>] [-c <n>] [-s <n>] [-r <n>] [-f <s>] [-b]
                [-w] [-t] [-n] [-l] [-a] [-v] [-d]
                input

positional arguments:
  input                 YM source file (must be extracted from within the
                        original YM file) [input]

optional arguments:
  -h, --help            show this help message and exit
  -o <output>, --output <output>
                        write VGM file <output> (default is '[input].vgm')
  -c <n>, --clock <n>   Set target SN76489 clock rate to <n> Mhz, default: 4.0
                        (4Mhz)
  -s <n>, --shift <n>   Set target SN76489 LFSR bit to <n> 15 or 16, default:
                        15 (BBC Micro)
  -r <n>, --rate <n>    Set envelope sample rate to <n> Hz (must be divisble
                        by 50!), default: 50Hz
  -f <s>, --filter <s>  Filter channels A,B,C,N <s> is a string, eg. -f AB
  -b, --bass            Enable software bass (output VGM will not be hardware
                        compliant for bass tones)
  -w, --white           Enable tuned white noise [experimental]
  -t, --attenuation     Force SN76489 attentuation mapping (volumes scaled
                        from YM dB to SN dB) [Experimental]
  -n, --noenvelopes     Disable envelope simulation
  -l, --loops           Export two VGM files, one for intro and one for
                        looping section
  -a, --arduino         Export Arduino BIN file
  -v, --verbose         Enable verbose mode
  -d, --debug           Enable debug mode

Notes:
 This tool does not support LHA compressed YM files, so
  you must extract the archived YM file first.

```
### **Important Note**:

* `.YM` files are actually *LHA compressed archives* which contain a single file (usually also called `.YM` or `.BIN`)
* This tool only works with the uncompressed inner YM file (as there are no Python LHA decoders I could find or easily install)
* Therefore it is necessary to manually extract the inner YM file to feed to this script (using 7zip or similar archive tool)


### Command Line Example:

To convert a `.YM` file to `.VGM`
```
ym2sn.py example/Androids.ym
```
Will output `example/Androids.vgm` using default settings of:
* periodic noise "simulated" bass
* envelope simulation
* 4Mhz clocked / 15-bit LFSR SN76489 (BBC Micro specs)
* 50Hz playback rate 

### Command Line Options & Effect Explanations

**Periodic Noise Bass**

The script will by default use SN periodic noise to simulate low frequency notes. It will interleave these noise effects with other channels as intelligently as possible, and generally achieves pleasing results.

Note however that that is a lossy approach and some notes on other channels may be switched around and/or sacrified to create space on channel C for the bass effect. Also note that since percussive noise effects take priority over simulated bass notes, the bass line may be interrupted to accommodate this. 

**Software Bass**

With this effect enabled, the script will not use periodic noise for bass frequencies, instead it will replace low frequency tones with "illegal" register data that contains additional flags for a software player to synthesize its own squarewave at the modified low frequency. Note that this feature will generate VGM outputs that do not sound correct.

**Tuned White Noise [EXPERIMENTAL]**

Normally the script will use the nearest "fixed frequency" white noise on the SN76489, which often is a decent approximation of the percussive sounds of the YM, however some subtlety is lost in the percussion.

If this feature is enabled, the script will attempt to interleave tuning for the noise channel to closely match the noise frequency set on the YM. Similarly to the periodic noise bass effect above, this effect will interleave the necessary tuning by sacrificing notes on tone channel C, which at the moment does not always sound ok (depending on the specific tune and which channels it uses) - hence the EXPERIMENTAL flag.





---

## YM Dump Utility

This repo also contains a utility script called `ymdump.py` which will take a `.YM` file containing `YM2149` music and export a `.YMR` file which is essentially the 'raw` YM2149 register data stored as 14 bytes x N frames (where 1 frame is a 50Hz or 60Hz update as defined as the playback rate in the source YM file).

This is useful for testing purposes or if you ever need to stream raw YM data to a chip, because YM files are often non-interleaved format which requires random access to the file rather than byte-streamed.

`ymdump.py` does not strip any `.YM` specific format data (such as the special case usage of unused bits in the sound chip register data).

### Example Usage

```
ymdump.py example\Androids.ym -o example\Androids.ymr
```


---

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
As we can see, the YM chip has a wider dynamic range than the SN chip in the low end.




## Conversion Process

What this all means is that it is reasonably easy to 'map' data for YM registers to data for SN registers, and recreate 'more-or-less' the same output.

The basic conversion process is as follows:
1. Scan through the YM file, one 50Hz frame at a time
1. Convert as best as possible the register updates going to the YM chip in each frame to an equivalent 'best fit' register set update for the SN chip
1. Map the 4-bit output levels directly for each channel from YM -> SN
1. Simulate the hardware envelopes, and apply attenuation as best we can* _(see below)_
1. If tone frequencies are too low for the SN chip, we map them to 'periodic noise' on the SN chip (typically bass lines), but give drum/noise sounds priority. OR we can use the software bass technique.
1. If tone frequencies are still too high or too low for the SN chip, bring them back into range an octave at a time
1. Where noise mixer is enabled, emulate this mix by computing aggregate volume for the SN noise channel across the 3 YM mixers -> 1 SN noise output
1. _Ignore digi-drums (for now)_
1. Emit a VGM file of the converted YM tune

Now we can play the conversion VGM in our favourite VGM player, or for playback on an actual SN chip, simply throw the packet of SN register writes (upto 11) at the chip each 50Hz frame and away we go.

### Example Conversions
See Bitshifters' [Twisted Brain Demo](https://bitshifters.github.io/posts/prods/bs-twisted-brain.html) for an example of a conversion of [Mad Max's epic 'There aren't any sheep in outer mongolia'](https://www.youtube.com/watch?v=2mwbBwAjt8c) tune. It's 3m33s of pretty busy sound chip updates compressed down to 22Kb and unpacked/streamed to the PSG at 50Hz, so not the most efficient data format, but it sounds surprisingly good for an SN chip.

Other examples of YM -> SN conversions via this script:
* Bitshifters [Wave Runner Demo](https://www.youtube.com/watch?v=1_nTOLdhQXY) - Conversion of Scavenger's awesome tune
* Bitshifters [Beeb-NICCC Demo](https://www.youtube.com/watch?v=_mVI9d2Acyw) - Conversion of an original YM composition of the Checknobankh MOD tune by Rhino
* Conversion of Mad Max's [Virtual Escape end music](https://www.youtube.com/watch?v=AGxVsh8ZCQI) by me, using the new tuned `--white` noise effect.


## Challenges
### Frequency Range
The SN76489 frequency range is determined by its clock speed, and for higher clocked systems (such as the BBC Micro at 4Mhz) it is not possible to reproduce lower frequencies. The script does its best to simulate these using periodic noise where appropriate, however a more accurate render can be generated by setting a lower SN clock speed.


### Using Periodic Noise for Bass
We can use the "tuned" periodic noise generator on the SN chip to allow us to 'simulate' that missing frequency range. This technique is the default setting for `sn2ym.py`, and generally (depending on the tune) delivers a much nicer & richer output.  

However, since there is only one periodic noise channel, we have to approximate bass as best we can - particularly in scenarios where multiple tone channels are playing low-frequency tones (in which case we pick the lowest frequency, with some bias toward the channel which we've determined contains the most "bass line" content). This technique generally works pretty well.

The other compromise that using periodic noise for bass presents, is that we have to share/interleave our bass notes with percussion (since both are effectively "noises" and we only have 1 noise channel). We give percussive sounds priority over bass, which generally gives pleasing results.

An important note here is that the tuning of Periodic noise on the SN76489 is influenced by which bit of the chip's LFSR (Linear feedback shift register) is tapped (bit 15 or bit 16).  The BBC Micro uses bit 15 (whereas other systems like the Sega Master System use bit 16). 

See [here for more info](https://gist.github.com/simondotm/84a08066469866d7f2e6aad875e598be) on that if you are interested.


### White Noise Limitations
The YM has 5-bit range for noise frequency, whereas the SN only has 3 "fixed frequency" white noise effects. For percussive effects this can sometimes result in a dull conversion where hit hats and snares tend to sound less crisp.

However, the SN does have a "tuned white noise" feature (similar to the tuned periodic noise above) so we can simulate all of the YM frequencies if we are prepared to sacrifice use a muted channel C to control the frequency of the white noise. 

In practice we can interleave this white noise tuning with most compositions without any noticeable side effects, and the result is much crisper percussion. The latest version of the script supports this optional effect (`--white`), but at the time of writing it is still a work in progress as some glitches need ironing out for how it re-allocates other channels without muting important notes on channel C when tuned percussion is playing.

### Software Simulated Bass

The low-end frequencies that SN76489 hardware cannot reproduce (when the SN76489 is clocked at typical rates) range between 30-122Hz. The good news is that these are the type of frequencies that humble 8-bit CPU's can cope with and most systems have hardware timers that can accurately implement interrupts at such frequencies without overwhelming the CPU.

It is therefore possible to reproduce these frequencies by implementing a "software square wave".

First we set a target tone register to the highest frequency (eg. 125Khz - effectively constanst level output) and then we modulate the channel's attenuation at a given frequency to synthesize the correct duty cycle for a low frequency squarewave tone to be output.  

Using this technique allows the full 12-bit range of the YM to be reproduced on the SN76489, and is a significant improvement over the periodic noise bass, since it does not limit bass to one channel or require the need to share the percussion/noise channel. 

*Credit, Kudos & Thanks goes to [@HexWab](https://github.com/hexwab) for realising and proving this particular innovation.*

#### Software Bass - Data Format

When the `-bass` option is enabled on the `ym2sn.py` commandline, the output VGM will be modified as follows:
* Any tone register low-end frequencies outside the 10-bit hardware range of the SN76489 are adjusted:
  * They are divided by 4 (2-bit shift down)
  * Bit 15 of the 2-byte (16-bit) tone register data value is set. (In practice this will be bit 6 of the second DATA byte sent to a tone register).

The software decoder must then interpret this bit as a "software bass" frequency and route the tone data to it's own squarewave generator instead of the hardware. 

If this bit is not set, the tone data can be sent directly to the hardware as usual.

Any VGM that is output using this setting will be VGM file-format compatible, but hardware-incompatible, so they will sound incorrect if played in a standard VGM player.  

*Note: Ensure your VGM file processing toolchain does not mask out this bit (for example some tools might do this to enforce the 10-bit tone register format of the SN76489).*

### Envelopes
Quite a lot of Atari ST tunes don't really use the envelope feature much, which makes conversions of these tunes a bit easier. 

However, the tunes that do use them, have a much different tonal landscape and sound pretty bad (harsh) when ported to the SN. 

That said, envelopes can be emulated as they are simple modulations of the volume for each tone generator. 

#### Envelope Emulation
This script supports emulation of the YM2149 envelope generator hardware, however it samples the current envelope output level at a default frequency of 50Hz, which means they are _highly approximated_, however even a simple approximation means that the output tune sounds much better than without any envelope consideration at all.

#### Envelope Sample Rate
There are routines implemented in the script that enable a higher sampling rate of the envelopes (for more accurate audio rendering), but be warned - they will generate very large VGM files, since the sound chip volumes need to be updated at a much higher frequency to allow for the high frequencies of the envelope modulator. There is further work to do on this feature, as the volumes are not filtered very accurately at the moment, so there's a lot of aliasing.

#### Envelope CPU emulation
In theory, envelopes could be supported CPU side, however supporting them will require a lot of CPU time in a chip tune player, since some of the envelopes in some tunes require updates at reasonably high frequencies - ie. upto tens of kilohertz. 

### Noise mixer
Some tunes use quite subtle combinations of the 3 noise mixers, which are very tricky if not impossible to 'fully' emulate on an SN chip, but when converted using a mixed approach, they don't sound too far off. Also worth noting that the YM and SN chips use different pseudo-white-noise generators so that changes tonal effect too.


### Digi drums
Digi drums were a later innovation in the Atari ST music journey. Essentially they are just envelopes that are driven by 4-bit waveform samples. The sample data is used to modulate the volume level for a tone generator in software rather than hardware. Again, these may be technically feasible for the SN chip, but like envelopes above, will require a lot of CPU time.

## Conclusions
So that pretty much sums it up. I was actually quite surprised how well the results turned out, so when I get chance I'll spend a bit more time on this as I think there are plenty of improvement areas!

## Related Tools & Projects
Other projects I've created for VGM:

* [VGM/VGC music player for the 6502 BBC Micro](https://github.com/simondotm/vgm-player-bbc)
* [VGM Compression tool for SN76489 VGM files](https://github.com/simondotm/vgm-packer)
* [VGM Conversion tool for targetting different SN76489 clock speeds](https://github.com/simondotm/vgm-converter)

## References

### Technical Info

See [SMS Power](http://www.smspower.org/Development/SN76489) for some great technical info about the SN76489 PSG.

Chip manufacturer data sheets are also very useful references:
* [SN76489](https://github.com/simondotm/ym2149f/blob/master/doc/SN76489.pdf)
* [YM2149](https://github.com/simondotm/ym2149f/blob/master/doc/ym2149_DS.pdf)

This project borrows from the good work by [FlorentFlament](https://github.com/FlorentFlament/ym2149-streamer).

### YM Music Archives
If you want to grab some YM files to play with, take a look [here](https://bulba.untergrund.net/music_e.htm).

Direct YM archive downloads:
* [CyBeR Goth's YMs](https://bulba.untergrund.net/faveym.7z) - Maybe not last edition. [Web-archive with source page](http://www.cybergoth.force9.co.uk/music.htm).
* [YM Archive v5](https://bulba.untergrund.net/YM_Archive_v5.7z) - YM-music archive, ST-Sound player author point to download it. Original archive was on ST Sound Plesuredome, its fragment in [web-archive](http://www.brainbug.ch/stsound).
* [Music in YM-format](https://bulba.untergrund.net/YM.7z) - (1997-1998) Archive with YM-music, years of YM-files creation are 1997-1998. No idea, where I've found it, and also where to find it now else.

Other resources relating to [Atari ST music](doc/Resources.md).
















