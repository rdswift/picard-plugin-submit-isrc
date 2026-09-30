#
# Copyright (C) 2020-2021, 2023, 2025-2026 Bob Swift (rdswift)
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, see <https://www.gnu.org/licenses/>.


import re

from PyQt6 import QtCore, QtWidgets

from picard.plugin3.api import (
    BaseAction,
    PluginApi,
    t_,
)
from picard.webservice.api_helpers import wrap_xml_metadata


RE_VALIDATE_ISRC = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')

XML_HEADER = '<recording-list>'
XML_TEMPLATE = '<recording id="{0}"><isrc-list count="1"><isrc id="{1}" /></isrc-list></recording>'
XML_FOOTER = '</recording-list>'


def validate_isrc(isrc):
    """Verify that the provided ISRC matches the standard pattern for a valid ISRC.

    Args:
        isrc (str): ISRC to validate

    Returns:
        str: Properly formatted ISRC (upper case with no spaces or hyphens) if valid, otherwise None
    """
    formatted_isrc = str(isrc).upper().replace(' ', '').replace('-', '')
    if re.match(RE_VALIDATE_ISRC, formatted_isrc):
        return formatted_isrc
    return None


def show_popup(title, content, window=None):
    """Display a pop-up dialog.

    Args:
        title (str): Title for the pop-up dialog.
        content (str): Text to be displayed in the pop-up dialog.
        window (object, optional): Parent object for the dialog. Defaults to None.
    """
    QtWidgets.QMessageBox.information(
        window,
        title,
        content,
        QtWidgets.QMessageBox.StandardButton.Ok,
        QtWidgets.QMessageBox.StandardButton.Ok
    )


class SubmitAlbumISRCs(BaseAction):
    TITLE = t_("action.title", "Submit ISRCs")

    def callback(self, albums):
        if not albums:
            self.api.logger.error("No album specified for submitting ISRCs.")
            return

        self.error_title = self.api.tr('message.error.title', 'Error')

        isrcs = {}
        multi_isrcs = []
        empty_albums = []
        processed_albums = 0

        self.api.logger.info("Submitting ISRCs for %d selected release(s).", len(albums))

        for album in albums:
            album_name = album.metadata.get('album', '')
            if not album.tracks:
                self.api.logger.debug("No tracks found in album: %s", album_name)
                empty_albums.append(album_name or self.api.tr('message.album.unknown', 'Unknown release'))
                continue

            processed_albums += 1

            for track in album.tracks:
                if not track.files:
                    continue

                audio_file = track.files[0]
                metadata = track.metadata
                file_metadata = audio_file.orig_metadata

                # No ISRC found in the file
                if 'isrc' not in file_metadata:
                    continue

                # Get string of existing ISRCs on MusicBrainz
                if 'isrc' in metadata:
                    mb_isrc = metadata['isrc'].upper()
                else:
                    mb_isrc = ''

                # Get ISRC string from the file
                file_isrc = file_metadata['isrc']

                # Multiple ISRCs found in the file (don't process)
                if ';' in file_isrc:
                    multi_isrcs.append(
                        f"  {album_name}: {metadata['tracknumber']} - {metadata['title']}"
                    )
                    self.api.logger.info(
                        "Multiple ISRCs found on track %s in %s (not processed): %s",
                        metadata['tracknumber'],
                        album_name,
                        file_isrc,
                    )
                    continue

                isrc = validate_isrc(file_isrc)

                # ISRC does not pass validation test
                if not isrc:
                    self.api.logger.debug(
                        "Invalid ISRC found on track %s in %s: %s",
                        metadata['tracknumber'],
                        album_name,
                        file_isrc,
                    )
                    show_popup(
                        self.error_title,
                        self.api.tr(
                            'message.error.invalid_isrc',
                            "Invalid ISRC found on track {track}: '{isrc}'"
                        ).format(track=metadata['tracknumber'], isrc=file_isrc)
                    )
                    return

                recording_id = metadata['musicbrainz_recordingid']

                # The same ISRC may legitimately appear in multiple selected releases
                # when they reference the same MusicBrainz recording. Only abort if the
                # same ISRC would be submitted to different recordings.
                if isrc in isrcs:
                    if isrcs[isrc] != recording_id:
                        self.api.logger.debug(
                            "Conflicting duplicate ISRC found on track %s in %s: %s",
                            metadata['tracknumber'],
                            album_name,
                            file_isrc,
                        )
                        show_popup(
                            self.error_title,
                            self.api.tr(
                                'message.error.duplicate_isrc',
                                "Duplicate ISRC found on track {track}: '{isrc}'"
                            ).format(track=metadata['tracknumber'], isrc=file_isrc)
                        )
                        return
                    continue

                # ISRC already associated with that track (MusicBrainz recording)
                if isrc in mb_isrc:
                    continue

                # New ISRC added for submission
                self.api.logger.debug(
                    "Adding ISRC '%s' for %s track %s - \"%s\"",
                    isrc,
                    album_name,
                    metadata['tracknumber'],
                    metadata['title'],
                )
                isrcs[isrc] = recording_id

        if processed_albums == 0:
            show_popup(
                self.error_title,
                self.api.tr('message.error.no_tracks', 'No tracks found in the selected releases.')
            )
            return

        notices = []

        if multi_isrcs:
            notices.append(
                self.api.tr(
                    'message.info.multiple_isrcs',
                    'The following tracks have multiple ISRCs and were not processed:'
                )
                + '\n'
                + '\n'.join(multi_isrcs)
            )

        if empty_albums:
            notices.append(
                self.api.tr(
                    'message.info.empty_releases',
                    'The following selected releases had no tracks and were skipped:'
                )
                + '\n  '
                + '\n  '.join(empty_albums)
            )

        notices_msg = ('\n\n' + '\n\n'.join(notices)) if notices else ''

        # Save counts to display in success message
        self.isrc_count = len(isrcs)
        self.album_count = processed_albums

        # Nothing to submit
        if not isrcs:
            self.api.logger.debug("No new ISRCs found in selected releases.")
            show_popup(
                self.error_title,
                self.api.tr(
                    'message.error.no_new_isrcs',
                    "No new ISRCs found for the tracks in the selected releases."
                )
                + notices_msg
            )
            return

        if notices_msg:
            show_popup(
                self.api.tr('message.submitting.title', 'Submitting'),
                self.api.trn(
                    'message.submitting.text',
                    singular="Submitting {n} ISRC.",
                    plural="Submitting {n} ISRCs.",
                    n=self.isrc_count,
                )
                + notices_msg
            )

        # Build the XML data payload
        xml_items = [XML_HEADER]
        for isrc, recording in isrcs.items():
            xml_items.append(XML_TEMPLATE.format(recording, isrc))
        xml_items.append(XML_FOOTER)
        data = wrap_xml_metadata(''.join(xml_items))

        # Initialize the MusicBrainz API Helper
        helper = self.api.mb_api

        # Set up parameters for the helper
        client_string = 'Picard_Plugin_{0}-v{1}'.format(
            'Submit ISRCs',
            self.api.get_plugin_version()
        ).replace(' ', '_')
        handler = self.submission_handler
        path = '/recording'
        params = {"client": client_string}

        return helper.post(
            path, data, handler, priority=True, queryargs=params, parse_response_type="xml",
            request_mimetype="application/xml; charset=utf-8")

    def submission_handler(self, document, reply, error):
        if not error:
            show_popup(
                self.api.tr('message.success.title', 'Success'),
                self.api.trn(
                    'message.success.text',
                    singular='Successfully submitted {n} ISRC.',
                    plural='Successfully submitted {n} ISRCs.',
                    n=self.isrc_count,
                )
                + self.api.trn(
                    'message.success.releases',
                    singular='\nProcessed {n} selected release.',
                    plural='\nProcessed {n} selected releases.',
                    n=self.album_count,
                )
            )
            return

        # Decode response if necessary.
        xml_text = str(document, 'UTF-8') if isinstance(document, (bytes, bytearray, QtCore.QByteArray)) else str(document)

        # Build error text message from returned XML payload
        err_text = ''
        matches = re.findall(r'<text>(.*?)</text>', xml_text)
        if matches:
            err_text = '\n'.join(matches)

        if not err_text:
            err_text = 'There was no error message provided.'

        show_popup(
            self.error_title,
            self.api.tr(
                'message.error.network',
                "There was an error processing the ISRC submission. Please try again.\n\nError Code: {error_code}\n\n{error_text}"
            ).format(error_code=error, error_text=err_text)
        )


def enable(api: PluginApi):
    """Called when plugin is enabled."""
    api.register_album_action(SubmitAlbumISRCs)
