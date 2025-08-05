async def run_timeline(shell, args):
    await shell._render_and_display_timeline(args)


async def run_watch(shell, args):
    await shell._start_live_timeline_view(args)
