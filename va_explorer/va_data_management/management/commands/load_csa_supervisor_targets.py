from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Alias for load_csa_target_supervisor_map. "
        "Loads CSA supervisor/target CSV mapping into the database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            nargs="?",
            help="Path to CSV file (defaults to MEDIA_ROOT/uploads/csa_target_supervisor_map.csv)",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete DB mapping rows not present in the input file.",
        )

    def handle(self, *args, **options):
        cmd_args = []
        if options.get("csv_file"):
            cmd_args.append(options["csv_file"])
        if options.get("prune"):
            cmd_args.append("--prune")

        call_command("load_csa_target_supervisor_map", *cmd_args)
